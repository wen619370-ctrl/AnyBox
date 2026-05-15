"""
DynamicToolDialog — 非模态工具执行弹窗

集成 DynamicUIBuilder（动态控件渲染）与 ScriptExecutor（沙盒脚本执行），
提供完整的"输入 → 运行 → 进度反馈 → 完成"交互闭环。

生命周期：
    1. 构造时接收 tool_id + ui_schema + script_path
    2. show() 后用户填写参数
    3. 点击【开始运行】→ 通过 UIBuilder.get_values() 提取参数
    4. 调用 ScriptExecutor.run_script() 启动异步执行
    5. 监听 executor 信号 → 更新进度条 / 解锁【打开文件夹】按钮
"""

import json
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QProgressBar,
    QLabel,
    QSizePolicy,
    QMessageBox,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from ui.tool_executor.ui_builder import DynamicUIBuilder, UISchemaItem
from core.executor import ScriptExecutor

logger = logging.getLogger("anybox.tool_dialog")


class DynamicToolDialog(QDialog):
    """
    非模态工具执行弹窗。

    布局结构（自上而下）：
        ┌──────────────────────────┐
        │  tool_name (QLabel 标题)  │
        │  description (QLabel)    │
        ├──────────────────────────┤
        │                          │
        │  DynamicUIBuilder 控件区  │  ← 根据 ui_schema 动态渲染
        │                          │
        ├──────────────────────────┤
        │  QProgressBar             │
        │  [开始运行]  [打开文件夹]  │  ← 底栏固定
        └──────────────────────────┘
    """

    # 默认尺寸
    DEFAULT_WIDTH = 520
    DEFAULT_MIN_HEIGHT = 360

    def __init__(
        self,
        tool_id: str,
        tool_name: str,
        description: str,
        ui_schema: list[UISchemaItem],
        script_path: str,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool_id = tool_id
        self._script_path = script_path
        self._output_dir: str = ""

        self.setWindowTitle(f"AnyBox · {tool_name}")
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_MIN_HEIGHT)
        self.setMinimumHeight(self.DEFAULT_MIN_HEIGHT)
        # 非模态：允许用户在脚本运行时操作主窗口
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        # ---- 子组件 ----
        self._ui_builder = DynamicUIBuilder()
        self._executor = ScriptExecutor(self)

        # ---- UI 控件引用（底栏） ----
        self._progress_bar: QProgressBar | None = None
        self._btn_run: QPushButton | None = None
        self._btn_open_dir: QPushButton | None = None

        self._setup_ui(tool_name, description, ui_schema)
        self._connect_executor()

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
    def _setup_ui(
        self, tool_name: str, description: str, ui_schema: list[UISchemaItem]
    ) -> None:
        """初始化完整布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---- 标题 ----
        title_lbl = QLabel(tool_name)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title_lbl)

        # ---- 描述 ----
        desc_lbl = QLabel(description)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #666; margin-bottom: 4px;")
        root.addWidget(desc_lbl)

        # ---- 动态控件区 ----
        dynamic_container = self._ui_builder.build(ui_schema)
        root.addWidget(dynamic_container, 1)  # stretch=1 占据剩余空间

        # ---- 进度条 ----
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        # ---- 底栏按钮 ----
        bar = QHBoxLayout()
        bar.setSpacing(10)

        bar.addStretch()

        self._btn_open_dir = QPushButton("📂 打开输出文件夹")
        self._btn_open_dir.setVisible(False)
        self._btn_open_dir.clicked.connect(self._on_open_output_dir)
        bar.addWidget(self._btn_open_dir)

        self._btn_run = QPushButton("▶ 开始运行")
        self._btn_run.setStyleSheet(
            "QPushButton { padding: 6px 24px; font-weight: bold; }"
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        bar.addWidget(self._btn_run)

        root.addLayout(bar)

    # ------------------------------------------------------------------
    # ScriptExecutor 信号连接
    # ------------------------------------------------------------------
    def _connect_executor(self) -> None:
        """将 ScriptExecutor 的所有业务信号绑定到 UI 槽函数。"""
        exe = self._executor
        exe.tool_started.connect(self._on_script_started)
        exe.tool_progress.connect(self._on_script_progress)
        exe.tool_done.connect(self._on_script_done)
        exe.tool_error.connect(self._on_script_error)
        exe.tool_finished.connect(self._on_script_finished)
        exe.tool_output_raw.connect(self._on_raw_output)

    # ------------------------------------------------------------------
    # 槽函数：用户交互
    # ------------------------------------------------------------------
    def _on_run_clicked(self) -> None:
        """【开始运行】按钮槽：提取参数并启动脚本。"""
        # 校验：上一次脚本是否仍在运行
        if self._executor.is_running:
            QMessageBox.information(self, "提示", "脚本正在运行中，请等待完成。")
            return

        # 提取用户参数
        params = self._ui_builder.get_values()
        params_json = json.dumps(params, ensure_ascii=False)

        # 启动脚本
        ok = self._executor.run_script(
            script_path=self._script_path,
            tool_id=self._tool_id,
            params_json=params_json,
        )

        if not ok:
            QMessageBox.warning(
                self,
                "启动失败",
                "脚本执行器未能正常启动，请检查沙盒 Python 环境与脚本路径。",
            )

    def _on_open_output_dir(self) -> None:
        """【打开输出文件夹】按钮槽：唤起系统资源管理器。"""
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))

    # ------------------------------------------------------------------
    # 槽函数：ScriptExecutor 信号回调
    # ------------------------------------------------------------------
    def _on_script_started(self, tool_id: str) -> None:
        """脚本启动：禁用运行按钮，显示进度条。"""
        logger.info(f"[Dialog] 脚本已启动: {tool_id}")
        if self._btn_run:
            self._btn_run.setEnabled(False)
            self._btn_run.setText("⏳ 运行中...")
        if self._progress_bar:
            self._progress_bar.setValue(0)
            self._progress_bar.setVisible(True)
        if self._btn_open_dir:
            self._btn_open_dir.setVisible(False)

    def _on_script_progress(self, tool_id: str, payload: dict) -> None:
        """进度更新：解析 payload 中的 percent 字段驱动进度条。"""
        percent = payload.get("percent")
        if isinstance(percent, (int, float)) and self._progress_bar:
            self._progress_bar.setValue(int(percent))

    def _on_script_done(self, tool_id: str, payload: dict) -> None:
        """脚本完成（协议消息）：记录 output_dir，留待 finished 信号时启用按钮。"""
        self._output_dir = payload.get("output_dir", "")
        logger.info(f"[Dialog] 脚本 done 信号: output_dir={self._output_dir}")

    def _on_script_error(self, tool_id: str, error_msg: str) -> None:
        """脚本错误：弹窗提示。"""
        logger.error(f"[Dialog] 脚本错误: {error_msg}")
        QMessageBox.critical(self, "脚本执行错误", error_msg)

    def _on_script_finished(self, tool_id: str, exit_code: int) -> None:
        """QProcess 终止（无论正常还是崩溃）：恢复 UI 状态。"""
        logger.info(f"[Dialog] 脚本结束: exit_code={exit_code}")
        if self._progress_bar:
            self._progress_bar.setVisible(False)
        if self._btn_run:
            self._btn_run.setEnabled(True)
            self._btn_run.setText("▶ 开始运行")

        # 如果有 output_dir 且文件存在，显示打开按钮
        if self._output_dir and Path(self._output_dir).is_dir():
            if self._btn_open_dir:
                self._btn_open_dir.setVisible(True)

    def _on_raw_output(self, tool_id: str, line: str) -> None:
        """非协议 stdout：仅记录 debug 日志。"""
        logger.debug(f"[Dialog:raw] {line}")