"""
ScriptExecutor — 沙盒脚本异步执行器

核心职责：
    1. 定位嵌入式 Python 解释器
    2. 使用 QProcess 非阻塞执行 AI 生成的 .py 脚本
    3. 解析 [[SYS_MSG]] 协议消息并通过 SignalBus 分发
    4. 超时监测与异常捕获
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, QProcessEnvironment, QThread

# ---------------------------------------------------------------------------
# 路径常量 — 与 main.py 保持同源
# ---------------------------------------------------------------------------
def _get_app_root() -> Path:
    """返回应用程序根目录。

    开发环境：当前文件所在目录的上两级 (项目根)。
    PyInstaller 打包后：exe 所在目录（而非临时解压目录 _MEIPASS）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _get_app_root()
DEFAULT_SANDBOX_DIR = ROOT_DIR / "sandbox" / "python-embed"

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
SYS_MSG_PREFIX = b"[[SYS_MSG]]"          # stdout 协议行前缀（字节形式，避免编解码问题）
DEFAULT_TIMEOUT_MS = 30 * 60 * 1000       # 默认超时：30 分钟

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logger = logging.getLogger("anybox.executor")


# ---------------------------------------------------------------------------
# ScriptExecutor
# ---------------------------------------------------------------------------
class ScriptExecutor(QObject):
    """
    封装 QProcess 的脚本执行器。

    QProcess 状态机（关键路径）：
        NotRunning → (start) → Starting → Running
            Running  → (正常退出) → NotRunning  (exitCode, exitStatus)
            Running  → (崩溃)     → NotRunning  (exitStatus = CrashExit)
            Running  → (错误)     → 内部调用 kill() → NotRunning

    信号发射时机：
        - tool_output_raw(int, str)    : 任何非协议 stdout 行
        - tool_progress(str, dict)     : 协议 __type__="progress"
        - tool_done(str, dict)         : 协议 __type__="done"
        - tool_error(str, str)         : 协议 __type__="error" 或 stderr 或超时
        - tool_started(str)            : QProcess state → Running
        - tool_finished(str, int)      : QProcess state → NotRunning (tool_id, exit_code)
    """

    # ---- 业务信号 ----
    tool_output_raw = Signal(str, str)          # (tool_id, raw_line)
    tool_progress = Signal(str, dict)           # (tool_id, parsed_json)
    tool_done = Signal(str, dict)               # (tool_id, parsed_json)
    tool_error = Signal(str, str)               # (tool_id, error_message)
    tool_started = Signal(str)                  # (tool_id,)
    tool_finished = Signal(str, int)            # (tool_id, exit_code)

    # ------------------------------------------------------------------
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._process: Optional[QProcess] = None
        self._timer: Optional[QTimer] = None
        self._dep_thread: Optional[QThread] = None
        self._dep_worker = None

        # 运行时状态
        self._tool_id: str = ""
        self._script_path: str = ""
        self._params_json: str = "{}"
        self._sandbox_dir: Optional[str] = None
        self._timeout_ms: int = DEFAULT_TIMEOUT_MS
        self._stdout_buffer: bytes = b""       # 按行拆分的残留缓冲区（原生 bytes）
        self._stderr_buffer: str = ""          # 收集 stderr 用于依赖分析

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def run_script(
        self,
        script_path: str,
        tool_id: str,
        params_json: str = "{}",
        *,
        sandbox_dir: Optional[str] = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> bool:
        """
        启动脚本异步执行。

        Args:
            script_path: .py 文件的绝对路径
            tool_id    : 唯一工具标识
            params_json: 用户输入的 JSON 字符串，以环境变量 ANYBOX_PARAMS 注入
            sandbox_dir: 嵌入式 Python 目录路径，None 则使用默认值
            timeout_ms : 超时毫秒数

        Returns:
            True 表示成功启动，False 表示启动失败（解释器不存在等）
        """
        # ---- 1. 定位解释器 ----
        interpreter = self._locate_python(sandbox_dir)
        if interpreter is None:
            logger.warning(
                "沙盒 Python 未找到 (%s)，回退为宿主 Python。"
                "生产环境请将 Embedded Python 放入沙盒目录。",
                DEFAULT_SANDBOX_DIR,
            )
            interpreter = sys.executable

        if not Path(script_path).is_file():
            msg = f"脚本文件不存在: {script_path}"
            logger.error(msg)
            self.tool_error.emit(tool_id, msg)
            return False

        # ---- 2. 校验 —— 同一时间仅允许一个脚本运行 ----
        if self.is_running:
            logger.warning("尝试启动新脚本，但上一个脚本仍在运行，将先终止旧进程。")
            self.kill()

        self._tool_id = tool_id
        self._script_path = script_path
        self._params_json = params_json
        self._sandbox_dir = sandbox_dir
        self._timeout_ms = timeout_ms
        self._stdout_buffer = b""
        self._stderr_buffer = ""

        # ---- 3. 启动依赖检查线程 ----
        self._start_dependency_check()
        return True

    def _start_dependency_check(self) -> None:
        """在后台线程中检查并安装脚本声明的依赖。"""
        self.tool_started.emit(self._tool_id)
        self.tool_progress.emit(self._tool_id, {"percent": 0, "message": "正在检查并安装依赖..."})

        self._dep_thread = QThread()
        self._dep_worker = _DependencyWorker(self._script_path, self._sandbox_dir)
        self._dep_worker.moveToThread(self._dep_thread)

        self._dep_thread.started.connect(self._dep_worker.run)
        self._dep_worker.finished.connect(self._on_dep_check_finished)
        self._dep_worker.error.connect(self._on_dep_check_error)

        self._dep_worker.finished.connect(self._dep_thread.quit)
        self._dep_worker.error.connect(self._dep_thread.quit)
        self._dep_worker.finished.connect(self._dep_worker.deleteLater)
        self._dep_worker.error.connect(self._dep_worker.deleteLater)
        self._dep_thread.finished.connect(self._dep_thread.deleteLater)

        self._dep_thread.start()

    def _on_dep_check_finished(self, installed: list[str]) -> None:
        if installed:
            logger.info(f"依赖安装完成: {installed}")
        self._start_process()

    def _on_dep_check_error(self, error_msg: str) -> None:
        logger.error(f"依赖检查失败: {error_msg}")
        self.tool_error.emit(self._tool_id, f"依赖检查失败: {error_msg}")
        self.tool_finished.emit(self._tool_id, -1)

    def _start_process(self) -> None:
        """实际启动 QProcess 执行脚本。"""
        # 发射 tool_started 信号 — 首次调用时 _start_dependency_check 也会发射，
        # 但依赖修复后自动重试时只有此处发射，确保 UI 可以正确重置状态
        self.tool_started.emit(self._tool_id)
        interpreter = self._locate_python(self._sandbox_dir) or sys.executable

        self._process = QProcess(self)

        # 环境变量：注入沙盒的 python 路径，确保脚本内部 sys.executable 指向沙盒
        env = QProcessEnvironment.systemEnvironment()
        env.insert("ANYBOX_PARAMS", self._params_json)
        env.insert("ANYBOX_SANDBOX", str(Path(interpreter).parent.parent))
        env.insert("PYTHONIOENCODING", "utf-8")               # 强制 UTF-8 输出
        self._process.setProcessEnvironment(env)

        # 将沙盒的 python 所在目录加入 PATH 开头，确保脚本内部 subprocess 调用也走沙盒
        bin_dir = str(Path(interpreter).parent)
        current_path = env.value("PATH", "")
        env.insert("PATH", f"{bin_dir}{';' if sys.platform == 'win32' else ':'}{current_path}")
        self._process.setProcessEnvironment(env)

        # ---- 4. 连接信号 ----
        # QProcess 生命周期
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_process_error)

        # 数据通道
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)

        # ---- 5. 启动子进程 ----
        logger.info(f"启动脚本: {self._script_path}  解释器: {interpreter}")
        self._process.setProgram(interpreter)
        self._process.setArguments([self._script_path])
        self._process.start()

        # ---- 6. 启动超时定时器 ----
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start(self._timeout_ms)

    def kill(self) -> None:
        """强制终止当前运行的脚本。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        if self._dep_thread is not None and self._dep_thread.isRunning():
            self._dep_thread.quit()
            self._dep_thread.wait(2000)

        if self._process is not None:
            state = self._process.state()
            if state != QProcess.ProcessState.NotRunning:
                logger.warning(f"强制终止脚本: {self._script_path}")
                self._process.kill()
                self._process.waitForFinished(3000)

    @property
    def is_running(self) -> bool:
        """当前是否有脚本正在运行。"""
        if self._dep_thread is not None and self._dep_thread.isRunning():
            return True
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    # ------------------------------------------------------------------
    # 内部方法：定位解释器
    # ------------------------------------------------------------------
    @staticmethod
    def _locate_python(sandbox_dir: str | None = None) -> str | None:
        """
        在沙盒目录中定位 python.exe 或 python (Unix)。

        搜索顺序：
            1. 如果 sandbox_dir 提供，优先使用
            2. 否则使用 DEFAULT_SANDBOX_DIR

        返回可执行文件路径，或 None。
        """
        base = Path(sandbox_dir) if sandbox_dir else DEFAULT_SANDBOX_DIR
        candidates = [
            base / "python.exe",           # Windows
            base / "python3.exe",
            base / "bin" / "python3",      # Unix-like
            base / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    # ------------------------------------------------------------------
    # 内部方法：定时器回调
    # ------------------------------------------------------------------
    def _on_timeout(self) -> None:
        """
        超时处理：
            1. 发送错误信号
            2. 强制 kill 子进程
            3. 发射 tool_finished 信号（exit_code = -1 表示超时）
        """
        msg = f"脚本执行超时 (>{DEFAULT_TIMEOUT_MS // 1000} 秒)，已强制终止。"
        logger.error(msg)
        self.tool_error.emit(self._tool_id, msg)
        self.kill()

    # ------------------------------------------------------------------
    # 内部方法：QProcess 信号槽
    # ------------------------------------------------------------------
    def _on_started(self) -> None:
        """QProcess 状态: NotRunning → Starting → Running"""
        logger.info(f"脚本进程已启动 (tool_id={self._tool_id})")
        # tool_started 已经在 _start_dependency_check 中发射过了
        # 这里可以发射一个进度更新
        self.tool_progress.emit(self._tool_id, {"percent": 5, "message": "脚本开始执行..."})

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """
        QProcess 状态: Running → NotRunning

        正常退出时 exit_status == NormalExit，exit_code 由脚本决定。
        崩溃时 exit_status == CrashExit。
        """
        # 清理定时器
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        # 处理残留缓冲区中未换行的最后一行
        if self._stdout_buffer:
            self._dispatch_line(self._stdout_buffer.decode("utf-8", errors="replace"))
            self._stdout_buffer = b""

        if exit_status == QProcess.ExitStatus.CrashExit or exit_code != 0:
            # 检查是否有缺失依赖
            if "ModuleNotFoundError" in self._stderr_buffer or "ImportError" in self._stderr_buffer:
                self.tool_progress.emit(self._tool_id, {"percent": 99, "message": "尝试自动修复缺失依赖..."})
                self._start_dependency_resolve()
                return

            msg = f"脚本异常退出 (exit_code={exit_code})，请检查 stderr 输出。"
            logger.error(msg)
            self.tool_error.emit(self._tool_id, msg)
            exit_code = -1

        logger.info(f"脚本结束 (tool_id={self._tool_id}, exit_code={exit_code})")
        self.tool_finished.emit(self._tool_id, exit_code)

    def _start_dependency_resolve(self) -> None:
        """在后台线程中尝试从 stderr 解析并安装缺失依赖。"""
        self._dep_thread = QThread()
        self._dep_worker = _DependencyResolveWorker(self._stderr_buffer, self._sandbox_dir)
        self._dep_worker.moveToThread(self._dep_thread)

        self._dep_thread.started.connect(self._dep_worker.run)
        self._dep_worker.finished.connect(self._on_dep_resolve_finished)
        self._dep_worker.error.connect(self._on_dep_resolve_error)

        self._dep_worker.finished.connect(self._dep_thread.quit)
        self._dep_worker.error.connect(self._dep_thread.quit)
        self._dep_worker.finished.connect(self._dep_worker.deleteLater)
        self._dep_worker.error.connect(self._dep_worker.deleteLater)
        self._dep_thread.finished.connect(self._dep_thread.deleteLater)

        self._dep_thread.start()

    def _on_dep_resolve_finished(self, installed: list[str]) -> None:
        if installed:
            msg = f"已自动安装缺失依赖: {', '.join(installed)}，正在自动重新运行..."
            logger.info(msg)
            self.tool_error.emit(self._tool_id, msg)
            # 自动重试：重新启动脚本（UI 侧 _on_script_finished 会先恢复按钮，
            # 但紧接着 _on_script_started 会再次禁用，进度条归零）
            self.tool_finished.emit(self._tool_id, -1)
            # 短暂延迟后自动重启，给 UI 事件循环处理 finished 信号的时间
            QTimer.singleShot(500, self._retry_script)
        else:
            msg = "脚本异常退出，且未能自动修复依赖。请检查日志。"
            self.tool_error.emit(self._tool_id, msg)
            self.tool_finished.emit(self._tool_id, -1)

    def _retry_script(self) -> None:
        """依赖修复后自动重新运行脚本。"""
        if Path(self._script_path).is_file():
            logger.info("自动重试脚本 | script=%s", self._script_path)
            self._start_process()
        else:
            logger.error("自动重试失败：脚本文件已不存在 | path=%s", self._script_path)

    def _on_dep_resolve_error(self, error_msg: str) -> None:
        logger.error(f"依赖修复失败: {error_msg}")
        self.tool_error.emit(self._tool_id, f"依赖修复失败: {error_msg}")
        self.tool_finished.emit(self._tool_id, -1)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        """
        QProcess 自身出错（非脚本逻辑错误），例如：
            - FailedToStart: 解释器不存在或无执行权限
            - Crashed     : 进程意外终止
        """
        error_map = {
            QProcess.ProcessError.FailedToStart: "进程启动失败——检查解释器路径与权限",
            QProcess.ProcessError.Crashed: "进程意外崩溃",
            QProcess.ProcessError.Timedout: "进程操作超时",
            QProcess.ProcessError.WriteError: "写入 stdin 时出错",
            QProcess.ProcessError.ReadError: "读取 stdout/stderr 时出错",
            QProcess.ProcessError.UnknownError: "未知进程错误",
        }
        desc = error_map.get(error, f"未知错误码: {error.value}")
        msg = f"QProcess 错误: {desc}"
        logger.error(msg)
        self.tool_error.emit(self._tool_id, msg)

    # ------------------------------------------------------------------
    # 内部方法：stdout 数据接收与行拆分
    # ------------------------------------------------------------------
    def _on_stdout(self) -> None:
        """
        由 readyReadStandardOutput 触发。
        从 QProcess 读取所有可用字节，追加到缓冲区，然后按 '\n' 拆分为完整行。
        每一行交给 _dispatch_line 处理。
        不完整的最后一行保留在缓冲区中等待下一次读取。
        """
        if self._process is None:
            return

        # readAllStandardOutput() 返回 QByteArray，需转为原生 bytes
        raw = self._process.readAllStandardOutput()
        chunk: bytes = bytes(raw) if raw else b""
        if not chunk:
            return

        self._stdout_buffer += chunk

        # 按行拆分：以 b'\n' 为分隔符
        while True:
            newline_idx = self._stdout_buffer.find(b"\n")
            if newline_idx == -1:
                break  # 没有完整行，保留在缓冲区

            line_bytes = self._stdout_buffer[:newline_idx]
            # 去掉尾部的 \r (Windows 兼容)
            if line_bytes.endswith(b"\r"):
                line_bytes = line_bytes[:-1]
            self._stdout_buffer = self._stdout_buffer[newline_idx + 1:]

            line_str = line_bytes.decode("utf-8", errors="replace")
            self._dispatch_line(line_str)

    # ------------------------------------------------------------------
    # 内部方法：单行分发
    # ------------------------------------------------------------------
    def _dispatch_line(self, line: str) -> None:
        """
        判断行内容：
            - 以 '[[SYS_MSG]]' 开头 → 协议消息 → 解析 JSON → 按 __type__ 分发信号
            - 否则 → 普通输出 → 通过 tool_output_raw 信号 + debug 日志输出
        """
        if line.startswith("[[SYS_MSG]]"):
            # 剥离前缀，取其后 JSON 内容
            json_str = line[len("[[SYS_MSG]]"):].strip()
            if not json_str:
                logger.warning(f"[{self._tool_id}] 收到空协议行")
                return

            try:
                payload: dict = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"[{self._tool_id}] 协议 JSON 解析失败: {e}  | raw: {json_str}")
                self.tool_output_raw.emit(self._tool_id, line)
                return

            # 按 __type__ 路由
            msg_type = payload.get("__type__", "")
            if msg_type == "progress":
                self.tool_progress.emit(self._tool_id, payload)
            elif msg_type == "done":
                self.tool_done.emit(self._tool_id, payload)
            elif msg_type == "error":
                err_detail = payload.get("message", json.dumps(payload))
                self.tool_error.emit(self._tool_id, err_detail)
            else:
                logger.info(f"[{self._tool_id}] 未知协议类型: {msg_type} | payload: {payload}")
                self.tool_output_raw.emit(self._tool_id, line)
        else:
            # 非协议行 → 原始输出
            logger.debug(f"[{self._tool_id}:stdout] {line}")
            self.tool_output_raw.emit(self._tool_id, line)

    # ------------------------------------------------------------------
    # 内部方法：stderr 接收
    # ------------------------------------------------------------------
    def _on_stderr(self) -> None:
        """
        由 readyReadStandardError 触发。
        读取所有 stderr 内容，逐行输出到日志，并合并后通过 tool_error 信号发出。
        stderr 通常包含 Traceback 等 Python 崩溃信息。
        """
        if self._process is None:
            return

        raw = self._process.readAllStandardError()
        chunk: bytes = bytes(raw) if raw else b""
        if not chunk:
            return

        text = chunk.decode("utf-8", errors="replace")
        self._stderr_buffer += text
        logger.error(f"[{self._tool_id}:stderr] {text}")
        self.tool_error.emit(self._tool_id, text.rstrip("\n"))


# ===================================================================
# 依赖安装工作线程
# ===================================================================
class _DependencyWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, script_path: str, sandbox_dir: str | None):
        super().__init__()
        self.script_path = script_path
        self.sandbox_dir = sandbox_dir

    def run(self):
        try:
            from core.dependency_manager import DependencyManager
            dm = DependencyManager(self.sandbox_dir)
            installed = dm.install_from_comments(self.script_path)
            self.finished.emit(installed)
        except Exception as e:
            self.error.emit(str(e))


class _DependencyResolveWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, stderr_text: str, sandbox_dir: str | None):
        super().__init__()
        self.stderr_text = stderr_text
        self.sandbox_dir = sandbox_dir

    def run(self):
        try:
            from core.dependency_manager import DependencyManager
            dm = DependencyManager(self.sandbox_dir)
            installed = dm.resolve_from_stderr(self.stderr_text)
            self.finished.emit(installed)
        except Exception as e:
            self.error.emit(str(e))