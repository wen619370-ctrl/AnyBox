"""
ToolCard — 工具卡片组件

用于 ToolLibraryView 网格布局中的单个工具展示卡片。

视觉风格：
    - 圆角边框卡片，hover 时轻微阴影
    - 顶部 Emoji 图标 + 工具名称
    - 中部描述文本
    - 底部【启动】按钮

信号：
    launch_requested(str) : 用户点击【启动】按钮，携带 tool_id
"""

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor


class ToolCard(QFrame):
    """
    工具卡片。

    用法：
        card = ToolCard(
            tool_id="1734567890123",
            tool_name="批量图片压缩",
            description="支持 JPG/PNG/WebP 格式的批量压缩工具",
        )
        card.launch_requested.connect(on_launch)
    """

    launch_requested = Signal(str)  # tool_id
    copy_prompt_requested = Signal(str)  # tool_id
    rename_requested = Signal(str)  # tool_id
    delete_requested = Signal(str)  # tool_id
    selection_toggled = Signal(str, bool)  # tool_id, is_now_selected

    CARD_WIDTH = 220
    CARD_HEIGHT = 200

    # 工具图标池（基于名称关键词匹配，兜底用 🔧）
    ICON_MAP = {
        "图片": "🖼️",
        "压缩": "🗜️",
        "视频": "🎬",
        "音频": "🎵",
        "文件": "📁",
        "重命名": "✏️",
        "批量": "📦",
        "转换": "🔄",
        "下载": "⬇️",
        "文本": "📝",
        "表格": "📊",
        "PDF": "📄",
        "合并": "🔗",
        "拆分": "✂️",
        "水印": "💧",
        "二维码": "📱",
        "备份": "💾",
        "清理": "🧹",
        "排序": "🔢",
        "搜索": "🔍",
        "加密": "🔐",
        "提取": "📤",
    }

    def __init__(
        self,
        tool_id: str,
        tool_name: str,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool_id = tool_id
        self._selected = False

        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("tool_card")
        self.setStyleSheet(self._card_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        # ---- 图标 ----
        icon = self._infer_icon(tool_name)
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont("Segoe UI Emoji", 26))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        # ---- 名称行（名称 + ✏️ 小图标） ----
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(2)

        name_lbl = QLabel(tool_name)
        name_lbl.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet("color: #333;")

        rename_icon_btn = QPushButton("✏️")
        rename_icon_btn.setFixedSize(20, 20)
        rename_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rename_icon_btn.setToolTip("重命名工具")
        rename_icon_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                font-size: 11px;
                color: #BBB;
            }
            QPushButton:hover {
                color: #1976D2;
                font-size: 13px;
            }
            """
        )
        rename_icon_btn.clicked.connect(lambda: self.rename_requested.emit(self._tool_id))

        name_row.addStretch()
        name_row.addWidget(name_lbl)
        name_row.addWidget(rename_icon_btn)
        name_row.addStretch()
        layout.addLayout(name_row)

        # ---- 描述 ----
        desc_lbl = QLabel(description)
        desc_lbl.setFont(QFont("Microsoft YaHei", 9))
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #888;")
        desc_lbl.setMaximumHeight(28)
        layout.addWidget(desc_lbl)

        layout.addStretch()

        # ---- 【启动】按钮 ----
        btn = QPushButton("🚀 启动")
        btn.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            """
            QPushButton {
                background: #1976D2;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 5px 0px;
            }
            QPushButton:hover {
                background: #1565C0;
            }
            QPushButton:pressed {
                background: #0D47A1;
            }
            """
        )
        btn.clicked.connect(lambda: self.launch_requested.emit(self._tool_id))
        layout.addWidget(btn)

        # ---- 辅助操作行（📋 复制提示词 | 🗑️ 删除） ----
        aux_row = QHBoxLayout()
        aux_row.setContentsMargins(0, 0, 0, 0)
        aux_row.setSpacing(6)

        copy_btn = QPushButton("📋")
        copy_btn.setFixedHeight(26)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setToolTip("复制提示词")
        copy_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: 1px solid #DDD;
                border-radius: 6px;
                font-size: 11px;
                color: #888;
            }
            QPushButton:hover {
                background: #F5F5F5;
                border: 1px solid #BBB;
                color: #333;
            }
            """
        )
        copy_btn.clicked.connect(lambda: self.copy_prompt_requested.emit(self._tool_id))

        del_btn = QPushButton("🗑️")
        del_btn.setFixedHeight(26)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("删除工具")
        del_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: 1px solid #EF9A9A;
                border-radius: 6px;
                font-size: 11px;
                color: #C62828;
            }
            QPushButton:hover {
                background: #FFEBEE;
                border: 1px solid #E57373;
            }
            """
        )
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._tool_id))

        aux_row.addWidget(copy_btn)
        aux_row.addWidget(del_btn)
        layout.addLayout(aux_row)

    # ------------------------------------------------------------------
    @property
    def tool_id(self) -> str:
        return self._tool_id

    # ------------------------------------------------------------------
    @property
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._update_style()
        self.selection_toggled.emit(self._tool_id, selected)

    # ------------------------------------------------------------------
    def _update_style(self) -> None:
        self.setStyleSheet(self._card_style())

    # ------------------------------------------------------------------
    @classmethod
    def _infer_icon(cls, name: str) -> str:
        """根据工具名称关键词返回对应 Emoji 图标。"""
        for keyword, icon in cls.ICON_MAP.items():
            if keyword in name:
                return icon
        return "🔧"

    # ------------------------------------------------------------------
    def _card_style(self) -> str:
        if self._selected:
            return """
                QFrame#tool_card {
                    background: #E3F2FD;
                    border: 2px solid #1976D2;
                    border-radius: 12px;
                }
                QFrame#tool_card:hover {
                    background: #BBDEFB;
                    border: 2px solid #1565C0;
                }
            """
        return """
            QFrame#tool_card {
                background: #FFFFFF;
                border: 1px solid #E0E0E0;
                border-radius: 12px;
            }
            QFrame#tool_card:hover {
                border: 1px solid #BBDEFB;
                background: #FAFAFA;
            }
        """
