"""
DynamicUIBuilder — JSON UI 协议 → PySide6 控件动态渲染引擎

将 AI 返回的 ui_schema 列表（见下方示例）即时映射为原生 PySide6 控件树，
并提供统一的数据提取接口。

示例 ui_schema:
[
    {"id": "input_dir",    "type": "folder_picker", "label": "选择源文件夹"},
    {"id": "output_format","type": "dropdown",      "label": "目标格式", "options": ["jpg","png","ico"]},
    {"id": "recursive",    "type": "checkbox",      "label": "包含子文件夹", "default": true},
    {"id": "description",  "type": "label",         "text": "请选择要处理的文件夹和输出格式。"},
]
"""

import logging
from typing import Any, Callable

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QListWidget,
    QSizePolicy,
)
from PySide6.QtCore import Signal, QObject
from PySide6.QtGui import QDragEnterEvent, QDropEvent

logger = logging.getLogger("anybox.ui_builder")

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------
UISchemaItem = dict[str, Any]           # 单个控件描述
ControlWidget = QWidget                  # 绑定 id 后的控件基类引用
ControlFactory = Callable[[UISchemaItem], ControlWidget]


# ---------------------------------------------------------------------------
# DynamicUIBuilder
# ---------------------------------------------------------------------------
class DynamicUIBuilder:
    """
    解析 ui_schema 列表，生成 QVBoxLayout 容器并持有控件引用。
    """

    # 垂直间距 (px)
    VERTICAL_SPACING = 8
    # 行内控件间距 (px)
    HORIZONTAL_SPACING = 6

    def __init__(self) -> None:
        # id → 用户交互控件 (QLineEdit / QComboBox / QCheckBox 等)
        self._widgets: dict[str, QWidget] = {}
        # id → 标签控件 (可选，用于后续 setText 等操作)
        self._labels: dict[str, QLabel] = {}

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def build(self, ui_schema: list[UISchemaItem]) -> QWidget:
        """
        将 ui_schema 渲染为带 QVBoxLayout 的容器控件。

        Args:
            ui_schema: AI 返回的 UI 描述列表

        Returns:
            QWidget: 包含所有控件的容器，可直接 addWidget 到任何布局中
        """
        # 清空旧状态，支持反复调用 build()
        self._widgets.clear()
        self._labels.clear()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.VERTICAL_SPACING)

        for item in ui_schema:
            widget_type = item.get("type", "")
            factory = _REGISTRY.get(widget_type)

            if factory is None:
                logger.warning(f"未知控件类型 '{widget_type}'，已跳过 (id={item.get('id', '?')})")
                continue

            # 工厂创建行容器，内部控件通过 objectName 标明其 id
            row_widget = factory(item)
            layout.addWidget(row_widget)

            # 注册：从行容器中抓取 objectName == id 的交互控件，存入 _widgets
            cid = item.get("id", "")
            if cid:
                bound = _find_bound_control(row_widget, cid)
                if bound is not None:
                    self._widgets[cid] = bound

        layout.addStretch()  # 底部弹性空间，避免控件被拉伸
        return container

    def get_values(self) -> dict[str, Any]:
        """
        遍历所有已创建控件，提取用户当前输入/选择的值。

        Returns:
            dict: { "control_id": value, ... }
        """
        result: dict[str, Any] = {}
        for cid, widget in self._widgets.items():
            value = _extract_value(widget)
            if value is not None:
                result[cid] = value
        return result


# ===================================================================
# 控件注册表
# ===================================================================
_REGISTRY: dict[str, ControlFactory] = {}


def _register(widget_type: str):
    """装饰器：将工厂函数注册到控件映射表。"""
    def decorator(fn: ControlFactory):
        _REGISTRY[widget_type] = fn
        return fn
    return decorator


# ===================================================================
# _DragDropListWidget — 支持拖放文件的 QListWidget 子类
# ===================================================================

class _DragDropListWidget(QListWidget):
    """支持从操作系统拖拽文件/文件夹到列表的 QListWidget 子类。

    通过重写 dragEnterEvent / dragMoveEvent / dropEvent 三件套实现。
    关键细节：在 dragEnterEvent 中必须同时调用 event.accept() 和
    event.acceptProposedAction()，缺一不可——

      - acceptProposedAction() 通知 Qt 我们接受的是 Copy/Move/Link 操作
      - accept() 接受事件本身（否则后续 dragMoveEvent/dropEvent 不会被投递）

    这是 PySide6/Qt 拖放最常见的坑：只调 acceptProposedAction() 不调 accept()
    会导致拖放看似"无效"——鼠标有禁止标志且 drop 永不触发。

    通过 path_added Signal 通知外部闭包添加路径，由外部决定路径
    有效性（目录或文件）。外部闭包须负责去重与 count 更新。
    """

    path_added = Signal(str)  # 拖入的有效本地路径

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    # ---- 拖放事件三件套 ----
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._event_has_local_file(event):
            event.acceptProposedAction()  # 接受操作提议（Copy/Move/Link）
            event.accept()                # ★ 接受事件本身（否则后续事件不到达）
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        """dragMoveEvent 必须接受，否则不会显示可放置光标且 drop 不触发。"""
        if self._event_has_local_file(event):
            event.acceptProposedAction()
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._event_has_local_file(event):
            from pathlib import Path as _P
            for url in event.mimeData().urls():
                local_path = url.toLocalFile()
                if local_path and _P(local_path).exists():
                    self.path_added.emit(str(_P(local_path).resolve()))
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---- 辅助 ----
    @staticmethod
    def _event_has_local_file(event) -> bool:
        """检查拖放事件中是否包含至少一个存在的本地文件/文件夹路径。"""
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return False
        from pathlib import Path as _P
        for url in mime.urls():
            if url.isLocalFile() and _P(url.toLocalFile()).exists():
                return True
        return False


# ===================================================================
# 值提取策略表
# ===================================================================
_EXTRACTORS: dict[type, Callable[[QWidget], Any]] = {}


def _extract_value(widget: QWidget) -> Any:
    """根据控件类型分发到对应提取器。"""
    extractor = _EXTRACTORS.get(type(widget))
    if extractor is not None:
        return extractor(widget)

    # 兜底：检查属性名 text/value
    # 注意 container_widget 类型不会被提取
    return None


# ===================================================================
# 控件工厂实现
# ===================================================================

@_register("text_input")
def _build_text_input(item: UISchemaItem) -> QWidget:
    """单行文本输入：QLineEdit"""
    cid = item["id"]
    label = item.get("label", cid)
    default = item.get("default", "")

    row, layout = _make_row()
    lbl = QLabel(label)
    edit = QLineEdit(str(default))
    edit.setObjectName(cid)

    _bind(cid, edit, lbl, item.get("placeholder"))

    layout.addWidget(lbl)
    layout.addWidget(edit, 1)
    return row


@_register("folder_picker")
def _build_folder_picker(item: UISchemaItem) -> QWidget:
    """文件夹选择器：QLineEdit + '浏览...' 按钮"""
    cid = item["id"]
    label = item.get("label", cid)

    row, layout = _make_row()
    lbl = QLabel(label)
    edit = QLineEdit()
    edit.setObjectName(cid)
    edit.setReadOnly(True)

    btn = QPushButton("浏览...")
    btn.clicked.connect(lambda: _pick_folder(edit))

    _bind(cid, edit, lbl)

    layout.addWidget(lbl)
    layout.addWidget(edit, 1)
    layout.addWidget(btn)
    return row


@_register("file_picker")
def _build_file_picker(item: UISchemaItem) -> QWidget:
    """文件选择器：QLineEdit + '选择文件...' 按钮"""
    cid = item["id"]
    label = item.get("label", cid)
    file_filter = item.get("filter", "所有文件 (*.*)")

    row, layout = _make_row()
    lbl = QLabel(label)
    edit = QLineEdit()
    edit.setObjectName(cid)
    edit.setReadOnly(True)

    btn = QPushButton("选择文件...")
    btn.clicked.connect(lambda: _pick_file(edit, file_filter))

    _bind(cid, edit, lbl)

    layout.addWidget(lbl)
    layout.addWidget(edit, 1)
    layout.addWidget(btn)
    return row


@_register("dropdown")
def _build_dropdown(item: UISchemaItem) -> QWidget:
    """下拉选择器：QComboBox"""
    cid = item["id"]
    label = item.get("label", cid)
    options = item.get("options", [])

    row, layout = _make_row()
    lbl = QLabel(label)
    combo = QComboBox()
    combo.setObjectName(cid)
    combo.addItems([str(o) for o in options])

    _bind(cid, combo, lbl)

    layout.addWidget(lbl)
    layout.addWidget(combo, 1)
    return row


@_register("checkbox")
def _build_checkbox(item: UISchemaItem) -> QWidget:
    """复选框：QCheckBox"""
    cid = item["id"]
    label = item.get("label", cid)
    default = item.get("default", False)

    row, layout = _make_row()
    chk = QCheckBox(label)
    chk.setObjectName(cid)
    chk.setChecked(bool(default))

    _bind(cid, chk)

    layout.addWidget(chk)
    layout.addStretch()
    return row


@_register("multi_folder_picker")
def _build_multi_folder_picker(item: UISchemaItem) -> QWidget:
    """多文件夹选择器：支持单个选择、批量选择（循环弹窗）、拖拽文件夹到列表。"""
    cid = item["id"]
    label = item.get("label", cid)

    row, layout = _make_row()

    # ---- 左侧标签 ----
    lbl = QLabel(label)
    layout.addWidget(lbl)

    # ---- 中间文件夹列表 ----
    list_widget = _DragDropListWidget()
    list_widget.setObjectName(cid)
    list_widget.setMaximumHeight(100)
    list_widget.setStyleSheet(
        "QListWidget { border: 1px solid #DDD; border-radius: 4px; "
        "background: #FAFAFA; }"
    )
    list_widget.setToolTip("可将文件夹直接拖拽到此列表")
    layout.addWidget(list_widget, 1)

    # ---- 右侧按钮组 ----
    btn_col = QWidget()
    btn_layout = QVBoxLayout(btn_col)
    btn_layout.setContentsMargins(0, 0, 0, 0)
    btn_layout.setSpacing(4)

    count_lbl = QLabel("已选 0 个")
    count_lbl.setStyleSheet("color: #888; font-size: 8pt;")

    add_single_btn = QPushButton("添加...")
    add_single_btn.setStyleSheet(
        "QPushButton { background: #E3F2FD; border: 1px solid #90CAF9; "
        "border-radius: 4px; padding: 3px 10px; font-size: 9pt; }"
        "QPushButton:hover { background: #BBDEFB; }"
    )
    add_single_btn.setToolTip("选择单个文件夹")

    add_batch_btn = QPushButton("批量添加...")
    add_batch_btn.setStyleSheet(
        "QPushButton { background: #E8F5E9; border: 1px solid #A5D6A7; "
        "border-radius: 4px; padding: 3px 10px; font-size: 9pt; font-weight: bold; }"
        "QPushButton:hover { background: #C8E6C9; }"
    )
    add_batch_btn.setToolTip("循环弹出对话框，可连续选择多个文件夹，点取消结束")

    clear_btn = QPushButton("清空")
    clear_btn.setStyleSheet(
        "QPushButton { background: transparent; border: 1px solid #DDD; "
        "border-radius: 4px; padding: 3px 10px; font-size: 9pt; color: #888; }"
        "QPushButton:hover { background: #FFF0F0; border-color: #E57373; color: #C0392B; }"
    )

    # 已加入路径集合（用于去重）
    added_paths: set[str] = set()

    def _update_count() -> None:
        count_lbl.setText(f"已选 {list_widget.count()} 个")

    def _add_one(path: str) -> None:
        """添加单个路径到列表（自动去重）。"""
        if path and path not in added_paths:
            from pathlib import Path as _P
            if not _P(path).is_dir():
                return  # multi_folder_picker 仅接受目录
            added_paths.add(path)
            list_widget.addItem(path)
            _update_count()

    def _add_single() -> None:
        path = QFileDialog.getExistingDirectory(row, "选择文件夹")
        _add_one(path)

    def _add_batch() -> None:
        """循环弹出文件夹对话框，直到用户点击取消。"""
        while True:
            path = QFileDialog.getExistingDirectory(
                row, "批量选择文件夹（点击取消结束）"
            )
            if not path:
                break
            _add_one(path)

    def _clear_folders() -> None:
        list_widget.clear()
        added_paths.clear()
        _update_count()

    # 拖拽：_DragDropListWidget 通过 path_added Signal 通知拖入路径，
    # 由 _add_one 负责过滤目录类型并去重
    list_widget.path_added.connect(_add_one)

    add_single_btn.clicked.connect(_add_single)
    add_batch_btn.clicked.connect(_add_batch)
    clear_btn.clicked.connect(_clear_folders)

    btn_layout.addWidget(add_batch_btn)
    btn_layout.addWidget(add_single_btn)
    btn_layout.addWidget(clear_btn)
    btn_layout.addStretch()
    layout.addWidget(btn_col)
    layout.addWidget(count_lbl)

    _bind(cid, list_widget, lbl)

    return row


@_register("label")
def _build_label(item: UISchemaItem) -> QWidget:
    """纯文本标签：QLabel (展示提示信息)"""
    text = item.get("text", item.get("label", ""))
    cid = item.get("id", "")
    word_wrap = item.get("word_wrap", True)

    row, layout = _make_row()
    lbl = QLabel(text)
    lbl.setWordWrap(word_wrap)
    lbl.setStyleSheet("color: #888; font-style: italic;")

    # label 类型通常不参与 get_values()，但仍绑定 id 以便外部引用
    if cid:
        lbl.setObjectName(cid)
        _bind(cid, lbl)

    layout.addWidget(lbl)
    return row


# ===================================================================
# 值提取器实现
# ===================================================================
_EXTRACTORS[QLineEdit] = lambda w: w.text()  # type: ignore[attr-defined]
_EXTRACTORS[QComboBox] = lambda w: w.currentText()  # type: ignore[attr-defined]
_EXTRACTORS[QCheckBox] = lambda w: w.isChecked()  # type: ignore[attr-defined]
_EXTRACTORS[QListWidget] = lambda w: [  # type: ignore[attr-defined]
    w.item(i).text() for i in range(w.count())
]
# label_widget 类型不参与提取（在 _extract_value 中已处理）


# ===================================================================
# multi_file_picker — 多文件选择器（支持拖拽和Ctrl/Shift多选）
# ===================================================================

@_register("multi_file_picker")
def _build_multi_file_picker(item: UISchemaItem) -> QWidget:
    """多文件选择器：支持单个选择（Ctrl+点击多选）、拖拽文件到列表。"""
    cid = item["id"]
    label = item.get("label", cid)
    file_filter = item.get("filter", "所有文件 (*.*)")

    row, layout = _make_row()

    # ---- 左侧标签 ----
    lbl = QLabel(label)
    layout.addWidget(lbl)

    # ---- 中间文件列表 ----
    list_widget = _DragDropListWidget()
    list_widget.setObjectName(cid)
    list_widget.setMaximumHeight(100)
    list_widget.setStyleSheet(
        "QListWidget { border: 1px solid #DDD; border-radius: 4px; "
        "background: #FAFAFA; }"
    )
    list_widget.setToolTip("可将文件直接拖拽到此列表\n按住 Ctrl 点击可多选，按住 Shift 可范围选择")
    layout.addWidget(list_widget, 1)

    # ---- 右侧按钮组 ----
    btn_col = QWidget()
    btn_layout = QVBoxLayout(btn_col)
    btn_layout.setContentsMargins(0, 0, 0, 0)
    btn_layout.setSpacing(4)

    count_lbl = QLabel("已选 0 个")
    count_lbl.setStyleSheet("color: #888; font-size: 8pt;")

    add_btn = QPushButton("添加文件...")
    add_btn.setStyleSheet(
        "QPushButton { background: #E8F5E9; border: 1px solid #A5D6A7; "
        "border-radius: 4px; padding: 3px 10px; font-size: 9pt; font-weight: bold; }"
        "QPushButton:hover { background: #C8E6C9; }"
    )
    add_btn.setToolTip("弹出文件对话框，支持 Ctrl+点击 / Shift+框选 一次选择多个文件")

    clear_btn = QPushButton("清空")
    clear_btn.setStyleSheet(
        "QPushButton { background: transparent; border: 1px solid #DDD; "
        "border-radius: 4px; padding: 3px 10px; font-size: 9pt; color: #888; }"
        "QPushButton:hover { background: #FFF0F0; border-color: #E57373; color: #C0392B; }"
    )

    # 已加入路径集合（用于去重）
    added_paths: set[str] = set()

    def _update_count() -> None:
        count_lbl.setText(f"已选 {list_widget.count()} 个")

    def _add_one(path: str) -> None:
        """添加单个文件路径到列表（自动去重）。"""
        if path and path not in added_paths:
            added_paths.add(path)
            list_widget.addItem(path)
            _update_count()

    def _add_files() -> None:
        """弹出文件多选对话框，支持 Ctrl/Shift 多选。"""
        paths, _ = QFileDialog.getOpenFileNames(
            row, "选择文件（可多选）", "", file_filter
        )
        for p in paths:
            _add_one(p)

    def _clear_files() -> None:
        list_widget.clear()
        added_paths.clear()
        _update_count()

    # 拖拽：_DragDropListWidget 通过 path_added Signal 通知拖入路径，
    # 由 _add_one 负责去重
    list_widget.path_added.connect(_add_one)

    add_btn.clicked.connect(_add_files)
    clear_btn.clicked.connect(_clear_files)

    btn_layout.addWidget(add_btn)
    btn_layout.addWidget(clear_btn)
    btn_layout.addStretch()
    layout.addWidget(btn_col)
    layout.addWidget(count_lbl)

    _bind(cid, list_widget, lbl)

    return row


# ===================================================================
# 内部辅助函数
# ===================================================================
def _make_row() -> tuple[QWidget, QHBoxLayout]:
    """创建统一行容器控件，返回 (row_widget, inner_layout) 元组。"""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(DynamicUIBuilder.HORIZONTAL_SPACING)
    return row, layout


def _bind(cid: str, widget: QWidget, label: QLabel | None = None,
          placeholder: str | None = None) -> None:
    """
    将控件注册到 builder 的 _widgets 字典中，以便后续 get_values() 提取。
    同时处理 placeholder 文本。
    """
    if placeholder:
        if hasattr(widget, 'setPlaceholderText'):
            widget.setPlaceholderText(placeholder)  # type: ignore[attr-defined]


def _pick_folder(target_edit: QLineEdit) -> None:
    """弹出文件夹选择对话框，将路径写入 target_edit。"""
    path = QFileDialog.getExistingDirectory(target_edit, "选择文件夹")
    if path:
        target_edit.setText(path)


def _pick_file(target_edit: QLineEdit, file_filter: str) -> None:
    """弹出文件选择对话框，将路径写入 target_edit。"""
    path, _ = QFileDialog.getOpenFileName(target_edit, "选择文件", "", file_filter)
    if path:
        target_edit.setText(path)


def _find_bound_control(parent: QWidget, target_cid: str) -> QWidget | None:
    """
    在 parent 的布局中递归查找 objectName == target_cid 的控件。
    
    控件工厂约定：交互控件（非 label）的 objectName 必须设置为其 id。
    """
    for child in parent.findChildren(QWidget):
        if child.objectName() == target_cid:
            return child
    return None