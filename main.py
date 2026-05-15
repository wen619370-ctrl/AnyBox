"""
AnyBox (万用沙盒) — 本地 AI 工具箱宿主程序
入口模块：负责应用生命周期管理、配置检测与视图路由。
"""

import sys
import os
import json
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QStatusBar,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QIcon

# ---------------------------------------------------------------------------
# 项目根路径常量
# ---------------------------------------------------------------------------
def _get_app_root() -> Path:
    """返回应用程序根目录。

    开发环境：当前文件所在目录 (项目根)。
    PyInstaller 打包后：exe 所在目录（而非临时解压目录 _MEIPASS）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT_DIR = _get_app_root()
CONFIG_FILE = ROOT_DIR / "anybox_config.json"
TOOLS_INDEX = ROOT_DIR / "tools_config.json"
SANDBOX_DIR = ROOT_DIR / "sandbox" / "python-embed"
TOOLS_DIR = ROOT_DIR / "tools"


def _get_resource_dir() -> Path:
    """返回资源文件目录（assets 等）。

    开发环境：项目根目录。
    PyInstaller 打包后：_MEIPASS 临时解压目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


RESOURCE_DIR = _get_resource_dir()

# ---------------------------------------------------------------------------
# 全局信号总线（解耦模块间通信）
# ---------------------------------------------------------------------------
class _SignalBus(QObject):
    """跨模块事件总线，所有模块通过此单例收发信号。"""
    onboarding_completed = Signal()                     # 向导完成
    tool_launch_requested = Signal(str)                 # 请求启动工具 (tool_id)
    tool_execution_finished = Signal(str, dict)         # 工具执行完毕 (tool_id, result)
    status_message = Signal(str, str)                   # 状态栏消息 (level, text)


signal_bus = _SignalBus()


# ===================================================================
# 应用全局样式表 (QSS)
# ===================================================================
GLOBAL_QSS = """
/* 全局字体 */
* {
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
}

/* 工具提示 */
QToolTip {
    background: #333333;
    color: #FFFFFF;
    border: 1px solid #555555;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 9pt;
    opacity: 230;
}

/* 主窗口背景 */
QMainWindow {
    background: #FAFAFA;
}

/* 状态栏 */
QStatusBar {
    background: #F0F0F0;
    border-top: 1px solid #DDD;
    color: #666;
    font-size: 9pt;
    padding: 2px 8px;
}

/* 滚动区域通用 */
QScrollArea {
    border: none;
    background: transparent;
}

/* 垂直滚动条 */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #CCC;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #AAA;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""


# ===================================================================
# 顶部 TabBar — 自定义视图切换控件
# ===================================================================
class _TabBar(QWidget):
    """
    自定义 TabBar，用于切换 QStackedWidget 页面。

    视觉风格：底部高亮指示器，hover 变色。
    右侧包含一个低调的「还原」按钮（开发/测试期使用，发布前可移除）。
    """

    tab_changed = Signal(int)           # 索引: 0=对话创造区, 1=工具库区
    reset_requested = Signal()          # 请求还原至初始状态
    settings_requested = Signal()       # 请求打开 API 配置对话框

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet("background: #FFFFFF; border-bottom: 1px solid #E0E0E0;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(4)

        self._tabs: list[QPushButton] = []
        self._active_index = 0

        # Tab 0: 对话创造区
        btn_chat = self._create_tab("💬 对话创造区")
        btn_chat.clicked.connect(lambda: self._set_active(0))
        self._tabs.append(btn_chat)
        layout.addWidget(btn_chat)

        # Tab 1: 工具库区
        btn_lib = self._create_tab("📦 工具库")
        btn_lib.clicked.connect(lambda: self._set_active(1))
        self._tabs.append(btn_lib)
        layout.addWidget(btn_lib)

        layout.addStretch()

        # ---- 设置按钮（右侧，⚙️ 齿轮图标） ----
        self._settings_btn = QPushButton("⚙️")
        self._settings_btn.setFont(QFont("Microsoft YaHei", 12))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setToolTip("修改 API 配置")
        self._settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #DDD;
                border-radius: 4px;
                padding: 3px 10px;
                color: #666;
            }
            QPushButton:hover {
                background: #E3F2FD;
                border-color: #1976D2;
                color: #1976D2;
            }
        """)
        self._settings_btn.clicked.connect(lambda: self.settings_requested.emit())
        layout.addWidget(self._settings_btn)

        # ---- 还原按钮（右侧，低调灰色） ----
        self._reset_btn = QPushButton("↺ 还原")
        self._reset_btn.setFont(QFont("Microsoft YaHei", 9))
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setToolTip("删除所有配置与工具数据，还原至初始状态")
        self._reset_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #DDD;
                border-radius: 4px;
                padding: 3px 12px;
                color: #999;
            }
            QPushButton:hover {
                background: #FFF0F0;
                border-color: #E57373;
                color: #C0392B;
            }
        """)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self._reset_btn)

        # 初始激活
        self._set_active(0)

    def _on_reset_clicked(self) -> None:
        """弹出二级确认对话框，确认后发射 reset_requested 信号。"""
        confirm = QMessageBox(self)
        confirm.setWindowTitle("还原确认")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText("确定要还原 AnyBox 至初始状态吗？")
        confirm.setInformativeText(
            "此操作将：\n"
            "  • 删除配置文件 anybox_config.json（含 API Key）\n"
            "  • 删除所有已生成工具的工具目录\n"
            "  • 清空工具索引 tools_config.json\n\n"
            "还原后应用将自动重启至初始化向导。"
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        confirm.button(QMessageBox.StandardButton.Ok).setText("确认还原")
        confirm.button(QMessageBox.StandardButton.Ok).setStyleSheet(
            "color: #C0392B; font-weight: bold;"
        )
        confirm.setEscapeButton(QMessageBox.StandardButton.Cancel)

        if confirm.exec() == QMessageBox.StandardButton.Ok:
            self.reset_requested.emit()

    def _create_tab(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(QFont("Microsoft YaHei", 11))
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-bottom: 3px solid transparent;
                padding: 8px 20px;
                color: #888;
                font-weight: normal;
            }
            QPushButton:hover {
                color: #333;
                background: #F5F5F5;
            }
            QPushButton:checked {
                color: #1976D2;
                border-bottom: 3px solid #1976D2;
                font-weight: bold;
            }
            """
        )
        return btn

    def _set_active(self, index: int) -> None:
        """激活指定索引的 Tab，取消其他。"""
        self._active_index = index
        for i, btn in enumerate(self._tabs):
            btn.setChecked(i == index)
        self.tab_changed.emit(index)


# ===================================================================
# 主窗口 — QStackedWidget 路由中枢
# ===================================================================
class MainWindow(QMainWindow):
    """
    双轨制主界面。

    使用 QStackedWidget 承载两个顶层视图：
      - 索引 0：对话创造区 (ChatCreateView)
      - 索引 1：工具库区   (ToolLibraryView)

    顶部通过自定义 TabBar 切换。
    """

    MIN_WIDTH = 1024
    MIN_HEIGHT = 680

    def __init__(self, api_config: dict | None = None) -> None:
        super().__init__()
        self.setWindowTitle("AnyBox · 万用沙盒")
        self.setWindowIcon(QIcon(str(RESOURCE_DIR / "assets" / "anybox.ico")))
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(1280, 800)

        # ---- 核心路由栈 ----
        self._stack = QStackedWidget(self)

        # ---- 视图引用 ----
        self._chat_view = None      # type: ChatCreateView | None
        self._tool_library = None   # type: ToolLibraryView | None

        # ---- API 配置 ----
        self._api_config = api_config or {}

        self._setup_ui()
        self._connect_signals()
        self._load_views()

    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        """构建完整布局：TabBar + QStackedWidget + StatusBar。"""
        # ---- 全局 QSS ----
        self.setStyleSheet(GLOBAL_QSS)

        # ---- 中心控件 ----
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # TabBar
        self._tab_bar = _TabBar(self)
        central_layout.addWidget(self._tab_bar)

        # StackedWidget
        central_layout.addWidget(self._stack, 1)

        self.setCentralWidget(central)

        # ---- 状态栏 ----
        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("就绪")

    def _connect_signals(self) -> None:
        """连接全局信号总线及内部信号。"""
        self._tab_bar.tab_changed.connect(self._on_tab_changed)
        self._tab_bar.reset_requested.connect(self._on_reset_app)
        self._tab_bar.settings_requested.connect(self._on_settings_clicked)
        signal_bus.tool_launch_requested.connect(self._on_launch_tool)
        signal_bus.status_message.connect(self._on_status_message)
        signal_bus.tool_execution_finished.connect(self._on_tool_finished)

    # ------------------------------------------------------------------
    # 视图延迟加载
    # ------------------------------------------------------------------
    def _load_views(self) -> None:
        """
        延迟加载两个主视图并注入 QStackedWidget。

        延迟导入避免启动时的非必要模块加载，同时规避循环依赖。
        """
        from ui.main_window.chat_view import ChatCreateView
        from ui.main_window.tool_library_view import ToolLibraryView

        # ---- 视图 A: 对话创造区 ----
        self._chat_view = ChatCreateView(self._api_config, self)
        self._stack.addWidget(self._chat_view)  # index 0

        # ---- 视图 B: 工具库区 ----
        self._tool_library = ToolLibraryView(self)
        self._tool_library.tool_launch_requested.connect(self._on_launch_tool)
        self._stack.addWidget(self._tool_library)  # index 1

        # 默认显示对话创造区
        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # 路由 API
    # ------------------------------------------------------------------
    def switch_to_chat(self) -> None:
        """切换到对话创造区。"""
        self._stack.setCurrentIndex(0)

    def switch_to_library(self) -> None:
        """切换到工具库区。"""
        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_tab_changed(self, index: int) -> None:
        """TabBar 切换时同步 QStackedWidget。"""
        self._stack.setCurrentIndex(index)
        self._status.showMessage(
            "对话创造区 · 用自然语言生成工具" if index == 0 else "工具库 · 管理已生成的工具"
        )

    def _on_settings_clicked(self) -> None:
        """打开 API 配置修改对话框，保存后热更新 ChatCreateView。"""
        from ui.main_window.settings_dialog import SettingsDialog

        dialog = SettingsDialog(current_config=self._api_config, parent=self)
        dialog.config_updated.connect(self._on_api_config_updated)
        dialog.exec()

    def _on_api_config_updated(self, new_config: dict) -> None:
        """API 配置保存后的回调：更新内存配置并通知 ChatCreateView 重建 LLM。"""
        self._api_config = new_config
        if self._chat_view is not None:
            self._chat_view.update_api_config(new_config)
        self._status.showMessage(
            f"✅ API 配置已更新 — {new_config.get('provider', '')} / {new_config.get('model', '')}",
            5000,
        )

    def _on_launch_tool(self, tool_id: str) -> None:
        """
        响应工具启动请求。

        从 tools/{tool_id}/config.json 读取 UI 描述，
        实例化 DynamicToolDialog 并以非模态方式展示。
        """
        from ui.tool_executor.dialog import DynamicToolDialog

        config_path = TOOLS_DIR / tool_id / "config.json"
        script_path = TOOLS_DIR / tool_id / "script.py"

        if not config_path.exists() or not script_path.exists():
            QMessageBox.warning(self, "启动失败", f"工具文件不完整 (tool_id={tool_id})")
            return

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.critical(self, "配置错误", f"无法解析工具配置:\n{e}")
            return

        meta = config.get("meta", {})
        ui_schema = config.get("ui", [])
        tool_name = meta.get("tool_name", "未命名工具")
        description = meta.get("description", "")

        dialog = DynamicToolDialog(
            tool_id=tool_id,
            tool_name=tool_name,
            description=description,
            ui_schema=ui_schema,
            script_path=str(script_path),
            parent=None,
        )
        dialog.show()

    def _on_status_message(self, level: str, text: str) -> None:
        """更新状态栏消息。"""
        self._status.showMessage(text)

    def _on_tool_finished(self, tool_id: str, result: dict) -> None:
        """
        工具执行完毕后回调：
          - 通知状态栏
          - 刷新工具库视图（如有）
        """
        tool_name = result.get("tool_name", "未知工具")
        success = result.get("success", False)
        if success:
            self._status.showMessage(f"✅ {tool_name} 执行完成", 5000)
        else:
            self._status.showMessage(f"❌ {tool_name} 执行失败", 5000)

        # 刷新工具库（如果已加载）
        if self._tool_library:
            self._tool_library.refresh()

    def _on_reset_app(self) -> None:
        """执行还原操作：清理持久化数据，然后重启应用到向导。"""
        import shutil

        # 1. 删除配置文件
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()

        # 2. 删除所有工具目录
        if TOOLS_DIR.exists():
            shutil.rmtree(TOOLS_DIR, ignore_errors=True)

        # 3. 删除工具索引
        if TOOLS_INDEX.exists():
            TOOLS_INDEX.unlink()

        # 4. 通知用户并重启
        QMessageBox.information(
            self,
            "还原完成",
            "所有配置与工具数据已清除。\n\n应用即将重启到初始化向导。",
        )

        # 延迟重启：关闭当前窗口，QApplication 检测到窗口关闭后会重新启动向导
        self.close()
        # 在 QApplication 层面重新触发向导
        _request_restart_to_onboarding()


# ===================================================================
# 配置检查器 — 决定启动流程
# ===================================================================
class ConfigGuard:
    """
    启动前哨：检测本地配置文件及 API Key 有效性。

    Methods:
        check() -> bool : True = needs onboarding, False = ready to launch
        load() -> dict  : 返回已解析的配置字典
    """

    @staticmethod
    def check() -> bool:
        """返回 True 表示需要走向导流程。"""
        if not CONFIG_FILE.exists():
            return True
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            api_key = config.get("api_key", "").strip()
            base_url = config.get("base_url", "").strip()
            if not api_key or not base_url:
                return True
        except (json.JSONDecodeError, OSError):
            return True
        return False

    @staticmethod
    def load() -> dict:
        """加载并返回配置字典，不存在时返回空字典。"""
        if not CONFIG_FILE.exists():
            return {}
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}


# ===================================================================
# 应用工厂
# ===================================================================
class AnyBoxApp:
    """
    应用程序顶层容器。

    职责：
      1. 初始化 QApplication
      2. 调用 ConfigGuard 判断走向导还是主界面
      3. 管理 OnboardingWizard ↔ MainWindow 的生命周期切换
    """

    def __init__(
        self,
        argv: list,
        qapp: QApplication | None = None,
    ) -> None:
        if qapp is not None:
            self._qapp = qapp
        else:
            self._qapp = QApplication(argv)
        self._qapp.setApplicationName("AnyBox")
        self._qapp.setStyle("Fusion")  # 跨平台风格一致性
        self._qapp.setWindowIcon(QIcon(str(RESOURCE_DIR / "assets" / "anybox.ico")))

        self._main_window: MainWindow | None = None
        self._wizard = None  # type: OnboardingWizard | None
        self._log = logging.getLogger("anybox.app")

    def launch(self) -> int:
        """入口：根据配置状态决定启动路径。"""
        if ConfigGuard.check():
            self._log.info("配置缺失或不完整，启动初始化向导")
            self._start_onboarding()
        else:
            self._log.info("配置有效，直接启动主窗口")
            self._start_main()
        return self._qapp.exec()

    # ------------------------------------------------------------------
    def _start_onboarding(self) -> None:
        """启动初始化向导。"""
        from ui.wizard.onboarding_wizard import OnboardingWizard

        self._wizard = OnboardingWizard()
        self._wizard.show()

        # 连接向导完成信号
        signal_bus.onboarding_completed.connect(self._on_onboarding_done)

    def _start_main(self) -> None:
        """启动主窗口（携带已加载的 API 配置）。"""
        api_config = ConfigGuard.load()
        self._main_window = MainWindow(api_config)
        self._main_window.show()

    def _on_onboarding_done(self) -> None:
        """向导完成后，关闭向导并启动主窗口。"""
        if self._wizard:
            self._wizard.close()
            self._wizard = None
        self._start_main()


# ---------------------------------------------------------------------------
# 还原重启支持
# ---------------------------------------------------------------------------
_restart_onboarding_flag = False


def _request_restart_to_onboarding() -> None:
    """
    设置还原标志为 True，并请求 QApplication 退出。

    调用方（MainWindow._on_reset_app）在设置此标志后应关闭主窗口。
    外部循环（main）检测到标志后重新启动向导。
    """
    global _restart_onboarding_flag
    _restart_onboarding_flag = True
    QApplication.instance().quit()


# ---------------------------------------------------------------------------
# 程序入口
# ---------------------------------------------------------------------------
def _setup_windows_app_id() -> None:
    """
    Windows 任务栏图标修复：将当前进程与应用 ID 绑定。

    Windows 默认将 python.exe 进程的任务栏图标设为 Python 图标。
    通过 SetCurrentProcessExplicitAppUserModelID 显式设置 AppID，
    Windows 会使用 QApplication 设置的窗口图标作为任务栏图标。
    此调用必须在 QApplication 创建之前执行。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        app_id = "LogicForge.AnyBox"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass  # 非关键路径，失败不影响应用启动


def main() -> None:
    # ═══════════════════════════════════════════════════════════
    # 第零步：Windows 应用 ID 绑定（必须在 QApplication 之前）
    # ═══════════════════════════════════════════════════════════
    _setup_windows_app_id()

    # ═══════════════════════════════════════════════════════════
    # 第一步：初始化日志系统
    # ═══════════════════════════════════════════════════════════
    from utils.logger import setup_logging

    setup_logging()
    _log = logging.getLogger("anybox.lifecycle")
    _log.info("AnyBox 启动 | PID=%d | Python=%s", os.getpid(), sys.executable)
    _log.info("项目根目录: %s", ROOT_DIR)

    # ═══════════════════════════════════════════════════════════
    # 第二步：创建 QApplication
    # ═══════════════════════════════════════════════════════════
    _qapp = QApplication(sys.argv)

    global _restart_onboarding_flag

    while True:
        _restart_onboarding_flag = False
        _log.info("创建 AnyBoxApp 实例 (restart_cycle=%s)", _restart_onboarding_flag)

        app = AnyBoxApp(sys.argv, qapp=_qapp)

        exit_code = app.launch()

        if not _restart_onboarding_flag:
            _log.info("AnyBox 正常退出 (exit_code=%d)", exit_code)
            sys.exit(exit_code)
        # 标志为 True → 循环回到顶部，重新启动应用（会走向导）
        _log.info("检测到还原重启标志，重新启动应用至向导")


if __name__ == "__main__":
    main()
