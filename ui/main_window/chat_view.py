"""
ChatCreateView — 对话创造区（多轮对话模式）

核心职责：
    1. 提供类 ChatGPT 的对话界面（消息气泡 + 输入区）
    2. 使用 chat_with_tool_detection() 进行多轮对话
    3. AI 回复可能包含纯文本、或"文本 + 工具 JSON"
    4. 检测到工具 JSON 时展示 ToolPreviewBubble（未保存草稿）
    5. 用户点击【💾 保存工具】后才持久化到 tools/ 并更新索引
    6. 同一次对话中的工具 JSON 更新会替换旧的 ToolPreviewBubble

消息气泡类型：
    - UserBubble           : 用户输入（右对齐，浅绿背景）
    - AssistantBubble      : AI 纯文本回复（左对齐，浅灰背景）
    - ToolPreviewBubble    : 工具草稿预览（橙色边框 + 【保存】【测试】按钮）
    - ThinkingBubble       : 等待 AI 响应（灰色闪烁动画）
"""

import json
import logging
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QTextEdit,
    QPushButton,
    QLabel,
    QFrame,
    QSizePolicy,
    QMessageBox,
    QApplication,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer, QEvent
from PySide6.QtGui import QFont

from core.llm_controller import LLMController, save_tool
from ui.tool_executor.dialog import DynamicToolDialog

# ---------------------------------------------------------------------------
# 路径常量 — 与 main.py 保持同源
# ---------------------------------------------------------------------------
def _get_app_root() -> Path:
    """返回应用程序根目录。

    开发环境：当前文件所在目录的上三级 (项目根)。
    PyInstaller 打包后：exe 所在目录（而非临时解压目录 _MEIPASS）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


ROOT_DIR = _get_app_root()
TOOLS_DIR = ROOT_DIR / "tools"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logger = logging.getLogger("anybox.chat_view")

# ---------------------------------------------------------------------------
# 颜色常量
# ---------------------------------------------------------------------------
COLOR_USER_BUBBLE = "#DCF8C6"
COLOR_AI_BUBBLE = "#F0F0F0"
COLOR_TOOL_BORDER = "#FF9800"       # 橙色 — 未保存工具
COLOR_SAVED_BORDER = "#4CAF50"      # 绿色 — 已保存工具
COLOR_THINKING_TEXT = "#999"


# ===================================================================
# LLM 工作线程（多轮对话版本）
# ===================================================================
class _LLMWorker(QObject):
    """在 QThread 中执行 LLMController.chat_with_tool_detection()。"""

    finished = Signal(dict)  # {"ok": bool, "text": str, "tool_json": dict|None, "raw": str}

    def __init__(self, llm: LLMController, user_input: str, history: list[dict]) -> None:
        super().__init__()
        self._llm = llm
        self._input = user_input
        self._history = history

    def run(self) -> None:
        logger.info(
            "LLM 工作线程开始 | input_len=%d | history_rounds=%d",
            len(self._input), len(self._history) // 2,
        )
        try:
            result = self._llm.chat_with_tool_detection(self._input, self._history)
            logger.info("LLM 工作线程完成 | ok=%s | has_tool=%s",
                        result.get("ok"), result.get("tool_json") is not None)
            self.finished.emit(result)
        except Exception:
            logger.exception("LLM 工作线程未捕获异常！")
            self.finished.emit({
                "ok": False,
                "text": "LLM 调用过程发生未预期的错误，请查看日志 (logs/anybox.log) 了解详情",
                "tool_json": None,
                "raw": "",
            })


# ===================================================================
# 消息气泡小组件
# ===================================================================
class _BubbleFrame(QFrame):
    """所有消息气泡的基类。"""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setContentsMargins(12, 8, 12, 8)


class UserBubble(_BubbleFrame):
    """用户消息 — 右对齐，浅绿背景，带 👤 头像。"""
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 头像行
        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        avatar_lbl = QLabel("👤 你")
        avatar_lbl.setFont(QFont("Microsoft YaHei", 8))
        avatar_lbl.setStyleSheet("color: #999; padding: 0px 4px;")
        avatar_row.addWidget(avatar_lbl)
        layout.addLayout(avatar_row)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setFont(QFont("Microsoft YaHei", 10))
        lbl.setStyleSheet("color: #333; padding: 4px;")
        layout.addWidget(lbl)
        self.setStyleSheet(
            f"QFrame#user_bubble {{ background: {COLOR_USER_BUBBLE}; "
            f"border-radius: 10px; }}"
        )
        self.setObjectName("user_bubble")


class AssistantBubble(_BubbleFrame):
    """AI 纯文本回复 — 左对齐，浅灰背景，带 🤖 AI 头像。"""
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 头像行
        avatar_row = QHBoxLayout()
        avatar_lbl = QLabel("🤖 AI")
        avatar_lbl.setFont(QFont("Microsoft YaHei", 8))
        avatar_lbl.setStyleSheet("color: #999; padding: 0px 4px;")
        avatar_row.addWidget(avatar_lbl)
        avatar_row.addStretch()
        layout.addLayout(avatar_row)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setFont(QFont("Microsoft YaHei", 10))
        lbl.setStyleSheet("color: #333; padding: 4px;")
        layout.addWidget(lbl)
        self.setStyleSheet(
            f"QFrame#assistant_bubble {{ background: {COLOR_AI_BUBBLE}; "
            f"border-radius: 10px; }}"
        )
        self.setObjectName("assistant_bubble")


class ThinkingBubble(_BubbleFrame):
    """等待 AI 响应 — 灰色闪烁文本。"""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel("🤔 AnyBox 正在思考中...")
        lbl.setFont(QFont("Microsoft YaHei", 10, italic=True))
        lbl.setStyleSheet(f"color: {COLOR_THINKING_TEXT}; padding: 4px;")
        layout.addWidget(lbl)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: lbl.setVisible(not lbl.isVisible()))
        self._timer.start(600)
        self.setStyleSheet(
            f"QFrame#thinking_bubble {{ background: {COLOR_AI_BUBBLE}; "
            f"border-radius: 10px; }}"
        )
        self.setObjectName("thinking_bubble")

    def stop(self) -> None:
        self._timer.stop()


class WarningBubble(_BubbleFrame):
    """JSON 解析失败时的黄色警告气泡，提示用户 AI 返回格式异常。"""
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        icon = QLabel("⚠️")
        icon.setFont(QFont("Segoe UI Emoji", 12))
        header.addWidget(icon)
        title = QLabel("工具 JSON 解析失败")
        title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #E65100; padding: 2px;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setFont(QFont("Microsoft YaHei", 9))
        lbl.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(lbl)

        self.setStyleSheet(
            "QFrame#warning_bubble { border: 1px solid #FFC107; "
            "border-radius: 10px; background: #FFF8E1; }"
        )
        self.setObjectName("warning_bubble")


# ===================================================================
# ToolPreviewBubble — 工具草稿预览（未保存）
# ===================================================================
class ToolPreviewBubble(_BubbleFrame):
    """
    检测到 AI 返回工具 JSON 时的预览气泡。

    初始状态（橙色边框）：
        - 工具名称 + 描述
        - 【💾 保存工具】按钮 → 持久化到磁盘，气泡变绿色
        - 【🚀 立即测试】按钮 → 启动非模态执行弹窗（保存前也可测试）
        - AI 原始 JSON 折叠查看

    保存后自动变为绿色边框，按钮文案变更。
    """

    def __init__(
        self,
        tool_id: str,
        tool_name: str,
        description: str,
        ui_schema: list[dict],
        script_path: str,
        raw_reply: str,
        dialog_keeper: list | None = None,
        user_prompt: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool_id = tool_id
        self._tool_name = tool_name
        self._description = description
        self._ui_schema = ui_schema
        self._script_path = script_path
        self._dialog_keeper = dialog_keeper or []
        self._user_prompt = user_prompt
        self._saved = False  # 是否已持久化

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # ---- 标题行 ----
        header = QHBoxLayout()
        self._icon_lbl = QLabel("🔧")
        self._icon_lbl.setFont(QFont("Segoe UI Emoji", 14))
        header.addWidget(self._icon_lbl)

        self._name_lbl = QLabel(f"[草稿] {tool_name}")
        self._name_lbl.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self._name_lbl.setStyleSheet("color: #E65100;")
        header.addWidget(self._name_lbl)
        header.addStretch()
        layout.addLayout(header)

        # ---- 描述 ----
        self._desc_lbl = QLabel(description)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setFont(QFont("Microsoft YaHei", 9))
        self._desc_lbl.setStyleSheet("color: #555; margin-left: 24px;")
        layout.addWidget(self._desc_lbl)

        # ---- 按钮栏 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_test = QPushButton("🚀 立即测试")
        self._btn_test.setStyleSheet(
            "QPushButton { background: #FF9800; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 6px; } "
            "QPushButton:hover { background: #F57C00; }"
        )
        self._btn_test.clicked.connect(self._on_test_clicked)
        btn_row.addWidget(self._btn_test)

        self._btn_save = QPushButton("💾 保存工具")
        self._btn_save.setStyleSheet(
            "QPushButton { background: #4CAF50; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 6px; } "
            "QPushButton:hover { background: #43A047; }"
        )
        self._btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self._btn_save)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ---- AI 原始 JSON（折叠） ----
        if raw_reply:
            self._raw_lbl = QLabel(raw_reply)
            self._raw_lbl.setWordWrap(True)
            self._raw_lbl.setFont(QFont("Consolas", 8))
            self._raw_lbl.setStyleSheet(
                "color: #666; background: #FAFAFA; border: 1px solid #DDD; "
                "border-radius: 4px; padding: 6px;"
            )
            self._raw_lbl.setVisible(False)
            self._raw_lbl.setMaximumHeight(120)

            toggle_btn = QPushButton("▼ 查看 AI 生成的 JSON")
            toggle_btn.setFlat(True)
            toggle_btn.setStyleSheet("color: #999; font-size: 9px;")
            toggle_btn.clicked.connect(self._toggle_raw)
            layout.addWidget(toggle_btn)
            layout.addWidget(self._raw_lbl)

        self._apply_style()

    # ------------------------------------------------------------------
    def _apply_style(self) -> None:
        border_color = COLOR_SAVED_BORDER if self._saved else COLOR_TOOL_BORDER
        bg_color = "#E8F5E9" if self._saved else "#FFF3E0"
        self.setStyleSheet(
            f"QFrame#tool_preview {{ border: 2px solid {border_color}; "
            f"border-radius: 12px; background: {bg_color}; }}"
        )
        self.setObjectName("tool_preview")

    # ------------------------------------------------------------------
    def update_tool(self, tool_id: str, tool_name: str, description: str,
                    ui_schema: list[dict], script_path: str, raw_reply: str) -> None:
        """AI 返回更新后的工具 JSON 时调用，更新气泡内容。"""
        self._tool_id = tool_id
        self._tool_name = tool_name
        self._description = description
        self._ui_schema = ui_schema
        self._script_path = script_path
        self._name_lbl.setText(f"[草稿] {tool_name}")
        # 同步更新描述标签
        self._desc_lbl.setText(description)
        # 更新原始回复显示
        if hasattr(self, '_raw_lbl'):
            self._raw_lbl.setText(raw_reply)
        # 重置保存状态
        self._saved = False
        self._icon_lbl.setText("🔧")
        self._btn_save.setText("💾 保存工具")
        self._btn_save.setEnabled(True)
        self._apply_style()

    # ------------------------------------------------------------------
    def _on_save_clicked(self) -> None:
        """保存工具到磁盘。"""
        if self._saved:
            return

        # 构造完整 JSON
        tool_json = {
            "meta": {
                "tool_id": self._tool_id,
                "tool_name": self._tool_name,
                "description": self._description,
            },
            "ui": self._ui_schema,
            "code": self._script_path,  # 实际持久化时从 script_path 读取
        }

        # 先把代码写入临时位置（save_tool 需要完整的 json_data）
        # 这里我们直接调用 save_tool 需要的格式
        # 但 save_tool 需要 code 字段是代码字符串，不是路径
        # 我们从 _script_path 读取代码内容
        try:
            code_content = Path(self._script_path).read_text(encoding="utf-8")
        except OSError:
            # 如果脚本文件不存在，从 ToolPreviewBubble 缓存里取
            # 实际上 chat_view 在创建 ToolPreviewBubble 时已写入临时脚本
            QMessageBox.critical(self, "保存失败", "脚本文件丢失，请重新生成工具。")
            return

        tool_json["code"] = code_content

        try:
            saved_id = save_tool(tool_json, user_prompt=self._user_prompt)
            logger.info(f"工具已保存: {saved_id}")
            self._saved = True
            self._tool_id = saved_id
            self._icon_lbl.setText("✅")
            self._name_lbl.setText(self._tool_name)
            self._name_lbl.setStyleSheet("color: #2E7D32;")
            self._btn_save.setText("✅ 已保存")
            self._btn_save.setEnabled(False)
            self._apply_style()
        except OSError as e:
            QMessageBox.critical(self, "保存失败", f"工具文件写入失败:\n{e}")

    # ------------------------------------------------------------------
    def _on_test_clicked(self) -> None:
        dialog = DynamicToolDialog(
            tool_id=self._tool_id,
            tool_name=self._tool_name,
            description=self._description,
            ui_schema=self._ui_schema,
            script_path=self._script_path,
            parent=None,
        )
        # 保持强引用，防止 GC 回收导致非模态弹窗不显示
        self._dialog_keeper.append(dialog)
        dialog.destroyed.connect(
            lambda d=dialog: (
                self._dialog_keeper.remove(d)
                if d in self._dialog_keeper
                else None
            )
        )
        dialog.show()
        logger.info(
            "从对话预览启动工具弹窗 | tool_id=%s | geometry=%s",
            self._tool_id, dialog.geometry(),
        )

    # ------------------------------------------------------------------
    def _toggle_raw(self) -> None:
        if hasattr(self, '_raw_lbl') and self._raw_lbl is not None:
            self._raw_lbl.setVisible(not self._raw_lbl.isVisible())

    # ------------------------------------------------------------------
    @property
    def tool_id(self) -> str:
        return self._tool_id


# ===================================================================
# ChatCreateView — 对话创造区主视图
# ===================================================================
class ChatCreateView(QWidget):
    """多轮对话创造区。"""

    DEFAULT_INPUT_HEIGHT = 80

    def __init__(self, api_config: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api_config = api_config
        self._llm = LLMController(api_config)

        # 对话历史 [{role: "user"|"assistant", content: "..."}]
        self._history: list[dict] = []

        # 当前会话中的未保存工具预览（同一对话内更新覆盖）
        self._current_tool_bubble: ToolPreviewBubble | None = None

        # thinking bubble
        self._thinking_bubble: ThinkingBubble | None = None

        # 当前会话中生成工具的用户原始自然语言需求（按 tool_id 缓存）
        self._user_prompt_map: dict[str, str] = {}

        # 后台线程引用
        self._worker_thread: QThread | None = None
        self._worker: _LLMWorker | None = None

        # 临时目录：工具草稿脚本存放处（保存前）
        self._draft_dir = TOOLS_DIR / "_drafts"
        self._draft_dir.mkdir(parents=True, exist_ok=True)

        # 保持对所有非模态弹窗的强引用，防止 GC 回收导致窗口消失
        self._active_dialogs: list[DynamicToolDialog] = []

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 消息历史滚动区 ----
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet("QScrollArea { border: none; background: #FAFAFA; }")

        self._msg_container = QWidget()
        self._msg_container.setObjectName("msg_container")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(16, 12, 16, 12)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()

        self._scroll_area.setWidget(self._msg_container)
        root.addWidget(self._scroll_area, 1)

        # ---- 底部输入栏 ----
        input_bar = QHBoxLayout()
        input_bar.setContentsMargins(12, 8, 12, 12)
        input_bar.setSpacing(8)

        self._input_edit = QTextEdit()
        self._input_edit.setPlaceholderText(
            "描述你想要的工具，例如：\"我想创建一个批量图片压缩的工具\"..."
        )
        self._input_edit.setFont(QFont("Microsoft YaHei", 10))
        self._input_edit.setAcceptRichText(False)
        self._input_edit.setMaximumHeight(self.DEFAULT_INPUT_HEIGHT)
        self._input_edit.setTabChangesFocus(True)
        self._input_edit.setStyleSheet(
            "QTextEdit { border: 1px solid #CCC; border-radius: 8px; "
            "padding: 8px 12px; background: white; }"
        )
        input_bar.addWidget(self._input_edit, 1)

        clear_btn = QPushButton("清空对话")
        clear_btn.setFont(QFont("Microsoft YaHei", 9))
        clear_btn.setFixedSize(72, 36)
        clear_btn.setStyleSheet(
            "QPushButton { background: #E0E0E0; color: #555; border-radius: 6px; "
            "border: 1px solid #CCC; } "
            "QPushButton:hover { background: #D0D0D0; } "
            "QPushButton:pressed { background: #BDBDBD; }"
        )
        clear_btn.clicked.connect(self._clear_conversation)
        input_bar.addWidget(clear_btn)

        send_btn = QPushButton("发送")
        send_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        send_btn.setFixedSize(64, 36)
        send_btn.setStyleSheet(
            "QPushButton { background: #2196F3; color: white; border-radius: 6px; } "
            "QPushButton:hover { background: #1E88E5; } "
            "QPushButton:pressed { background: #1976D2; }"
        )
        send_btn.clicked.connect(self._on_send_clicked)
        input_bar.addWidget(send_btn)

        root.addLayout(input_bar)

        self._input_edit.installEventFilter(self)
        self._send_btn = send_btn
        self._clear_btn = clear_btn

    # ------------------------------------------------------------------
    # 事件过滤器：回车发送
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        if obj is self._input_edit and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if (
                key_event.key() == Qt.Key.Key_Return
                and key_event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 发送逻辑
    # ------------------------------------------------------------------
    def _on_send_clicked(self) -> None:
        text = self._input_edit.toPlainText().strip()
        if not text:
            return

        logger.info("用户发送消息 | len=%d | preview=%.80s...", len(text), text)

        self._input_edit.clear()
        self._input_edit.setEnabled(False)
        self._send_btn.setEnabled(False)

        # 展示用户气泡
        self._add_bubble(UserBubble(text), align_right=True)
        self._history.append({"role": "user", "content": text})

        # 展示 thinking 气泡
        thinking = ThinkingBubble()
        self._thinking_bubble = thinking
        self._add_bubble(thinking, align_right=False)
        self._scroll_to_bottom()

        # 启动后台 LLM
        self._start_llm_request(text)

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------
    def _start_llm_request(self, user_input: str) -> None:
        self._cleanup_previous_worker()

        worker = _LLMWorker(self._llm, user_input, self._history)
        thread = QThread(self)

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_llm_result)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_worker", None))
        thread.finished.connect(lambda: setattr(self, "_worker_thread", None))

        self._worker = worker
        self._worker_thread = thread
        thread.start()

    def _cleanup_previous_worker(self) -> None:
        prev_worker = self._worker
        prev_thread = self._worker_thread
        if prev_thread is not None and prev_thread.isRunning():
            if prev_worker is not None:
                try:
                    prev_worker.finished.disconnect(self._on_llm_result)
                except (RuntimeError, TypeError):
                    pass
            prev_thread.quit()
            if not prev_thread.wait(1000):
                prev_thread.terminate()
                prev_thread.wait(2000)

    # ------------------------------------------------------------------
    # LLM 结果处理（主线程回调）
    # ------------------------------------------------------------------
    def _on_llm_result(self, result: dict) -> None:
        logger.info("LLM 结果回调（主线程） | ok=%s | has_tool=%s",
                    result.get("ok"), result.get("tool_json") is not None)

        # 移除 thinking 气泡
        if self._thinking_bubble is not None:
            self._thinking_bubble.stop()
            self._thinking_bubble.setVisible(False)
            self._msg_layout.removeWidget(self._thinking_bubble)
            self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

        # 恢复输入区
        self._input_edit.setEnabled(True)
        self._send_btn.setEnabled(True)

        if not result.get("ok"):
            # 网络错误等：仅展示错误气泡，不污染对话历史
            # 避免将机器错误（如超时、API Key 无效）混入后续 LLM 上下文
            error_text = result.get("text", "未知错误")
            self._add_bubble(AssistantBubble(f"❌ {error_text}"), align_right=False)
            # 移除用户刚发的消息，因为它的回复失败了
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            self._scroll_to_bottom()
            return

        ai_text = result.get("text", "")
        tool_json = result.get("tool_json")
        raw_reply = result.get("raw", "")
        parse_warning = result.get("parse_warning", "")

        # 始终展示 AI 文本回复
        if ai_text:
            self._add_bubble(AssistantBubble(ai_text), align_right=False)
            # 历史中只记录文本部分（tool_json 通过界面展示）
            self._history.append({"role": "assistant", "content": ai_text})

        # 如果有工具 JSON
        if tool_json is not None:
            self._handle_tool_json(tool_json, raw_reply)

        # 如果 AI 回复中包含工具 JSON 特征但解析失败，给用户可见的黄色警告
        if parse_warning:
            warning_bubble = WarningBubble(parse_warning)
            self._add_bubble(warning_bubble, align_right=False)

        self._scroll_to_bottom()

    # ------------------------------------------------------------------
    # 工具 JSON 处理
    # ------------------------------------------------------------------
    def _get_last_user_message(self) -> str:
        """从历史记录中获取最近一条用户消息（作为创建工具的提示词）。"""
        for msg in reversed(self._history):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    def _handle_tool_json(self, tool_json: dict, raw_reply: str) -> None:
        meta = tool_json["meta"]
        ui = tool_json["ui"]
        code = tool_json["code"]

        tool_name = meta.get("tool_name", "未命名工具")
        description = meta.get("description", "")
        tool_id = meta.get("tool_id", str(int(time.time() * 1000)))

        logger.info(
            "处理工具 JSON | tool_name=%s | ui_controls=%d | code_len=%d | "
            "has_existing_bubble=%s | existing_tool_name=%s",
            tool_name,
            len(ui),
            len(code),
            self._current_tool_bubble is not None,
            self._current_tool_bubble._tool_name
            if self._current_tool_bubble is not None
            else "无",
        )

        # 写入草稿脚本文件（用户可以立即测试）
        draft_path = self._draft_dir / f"{tool_id}.py"
        try:
            draft_path.write_text(code, encoding="utf-8")
            logger.debug(
                "草稿脚本已写入 | path=%s | size=%d bytes",
                draft_path, len(code.encode("utf-8")),
            )
        except OSError as e:
            logger.error("草稿脚本写入失败 | path=%s | error=%s", draft_path, e)
            QMessageBox.critical(self, "写入失败", f"无法写入临时脚本:\n{e}")
            return

        script_path = str(draft_path)

        # 如果已有当前会话的 ToolPreviewBubble，更新它；否则新建
        if self._current_tool_bubble is not None:
            logger.info(
                "更新已有工具预览 | old_name=%s → new_name=%s | "
                "new_ui_controls=%d | new_code_len=%d",
                self._current_tool_bubble._tool_name,
                tool_name,
                len(ui),
                len(code),
            )
            self._current_tool_bubble.update_tool(
                tool_id, tool_name, description, ui, script_path, raw_reply,
            )
        else:
            # 首次创建工具 → 记录用户原始提示词
            user_prompt = self._get_last_user_message()
            self._user_prompt_map[tool_id] = user_prompt
            logger.info(
                "首次创建工具预览 | tool_name=%s | prompt_len=%d | "
                "prompt_preview=%.80s...",
                tool_name, len(user_prompt), user_prompt,
            )
            bubble = ToolPreviewBubble(
                tool_id=tool_id,
                tool_name=tool_name,
                description=description,
                ui_schema=ui,
                script_path=script_path,
                raw_reply=raw_reply,
                dialog_keeper=self._active_dialogs,
                user_prompt=user_prompt,
            )
            self._current_tool_bubble = bubble
            self._add_bubble(bubble, align_right=False)
            logger.debug("ToolPreviewBubble 已添加到消息布局 | tool_name=%s", tool_name)

    # ------------------------------------------------------------------
    # API 配置热更新
    # ------------------------------------------------------------------
    def update_api_config(self, new_config: dict) -> None:
        """热更新 LLM API 配置并清空对话历史。

        当用户在主界面修改 API 配置时调用，会：
        1. 重新创建 LLMController 实例
        2. 清空对话历史（避免新旧 API 上下文混淆）
        3. 清空当前会话的工具预览
        """
        logger.info(
            "ChatCreateView 热更新 API 配置 | provider=%s | model=%s",
            new_config.get("provider"), new_config.get("model"),
        )

        self._api_config = new_config
        self._llm = LLMController(new_config)

        # 清空对话历史与 UI
        self._history.clear()
        self._current_tool_bubble = None
        self._user_prompt_map.clear()

        # 清空消息容器中的所有气泡（保留最后的 stretch）
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.layout():
                self._clear_layout(item.layout())
            elif item.widget():
                item.widget().deleteLater()

        logger.debug("对话历史与 UI 已清空，LLM 控制器已重建")

    @staticmethod
    def _clear_layout(layout) -> None:
        """递归清理 layout 中的所有子控件。"""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                ChatCreateView._clear_layout(item.layout())

    def _clear_conversation(self) -> None:
        """清空当前对话：清除所有消息气泡、清空对话历史、重置工具预览状态。"""
        if not self._history:
            return  # 已经是空的，无需操作

        reply = QMessageBox.question(
            self,
            "清空对话",
            "确定要清空当前对话吗？\n所有未保存的工具草稿将会丢失。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        logger.info("用户确认清空对话 | history_rounds=%d", len(self._history) // 2)

        self._history.clear()
        self._current_tool_bubble = None
        self._user_prompt_map.clear()

        # 清空消息容器中的所有气泡（保留最后的 stretch）
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.layout():
                self._clear_layout(item.layout())
            elif item.widget():
                item.widget().deleteLater()

        logger.debug("对话已清空")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _add_bubble(self, bubble: QFrame, *, align_right: bool = False) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        if align_right:
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()

        self._msg_layout.insertLayout(self._msg_layout.count() - 1, row)

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(50, self._do_scroll)

    def _do_scroll(self) -> None:
        sb = self._scroll_area.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())