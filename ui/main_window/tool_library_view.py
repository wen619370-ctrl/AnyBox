"""
ToolLibraryView — 工具库区

核心职责：
    1. 读取 ROOT_DIR/tools_config.json 索引文件
    2. 使用 QScrollArea + FlowLayout 以卡片网格展示所有已生成工具
    3. 每张 ToolCard 包含工具名称、描述、图标、信息按钮、【启动】按钮
    4. 点击【启动】→ 读取 tools/{tool_id}/config.json → 实例化 DynamicToolDialog
    5. 点击【信息】→ 弹出工具详情（后续可扩展编辑）
    6. 空状态提示：尚无工具时显示引导文案

布局结构：
    ┌──────────────────────────────────┐
    │  📦 工具库                    🔄  │  ← 标题 + 刷新按钮
    ├──────────────────────────────────┤
    │  QScrollArea                     │
    │  ┌──────┐ ┌──────┐ ┌──────┐     │
    │  │ Card │ │ Card │ │ Card │ ... │  ← FlowLayout 网格
    │  └──────┘ └──────┘ └──────┘     │
    └──────────────────────────────────┘
"""

import json
import logging
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QApplication,
    QMessageBox,
    QLineEdit,
    QInputDialog,
    QRubberBand,
)
from PySide6.QtGui import QClipboard
from PySide6.QtCore import Qt, QTimer, Signal, QRect, QPoint, QEvent
from PySide6.QtGui import QFont

from ui.main_window.tool_card import ToolCard
from ui.tool_executor.dialog import DynamicToolDialog
from ui.tool_executor.ui_builder import UISchemaItem

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
TOOLS_INDEX = ROOT_DIR / "tools_config.json"
TOOLS_DIR = ROOT_DIR / "tools"

logger = logging.getLogger("anybox.tool_library")


# ===================================================================
# 简易 FlowLayout（基于 QVBoxLayout + QHBoxLayout 模拟流式网格）
# ===================================================================
class _FlowGridLayout:
    """
    模拟 FlowLayout 的网格布局管理器。

    由于 PySide6 没有内置 FlowLayout，这里用嵌套的 QHBoxLayout 行
    来模拟自动换行网格。每行放满后自动折行。

    用法：
        grid = _FlowGridLayout(container_layout, card_width=220, spacing=16)
        grid.add_card(card)
        grid.clear()
    """

    def __init__(
        self,
        container: QVBoxLayout,
        card_width: int = 220,
        spacing: int = 16,
        columns: int | None = None,
    ) -> None:
        self._container = container
        self._card_width = card_width
        self._spacing = spacing
        self._columns = columns  # None → 自适应
        self._rows: list[QHBoxLayout] = []

    def add_card(self, card: ToolCard) -> None:
        """将卡片添加到当前行（满则换行）。"""
        # 查找可以容纳新卡片的行
        target_row = None
        for row in self._rows:
            current_count = row.count()
            if self._columns and current_count >= self._columns:
                continue
            # 估算当前行宽度
            target_row = row
            break

        if target_row is None:
            # 创建新行
            target_row = QHBoxLayout()
            target_row.setContentsMargins(0, 0, 0, 0)
            target_row.setSpacing(self._spacing)
            target_row.addStretch()  # 末尾弹性空间
            self._container.addLayout(target_row)
            self._rows.append(target_row)

        # 在 stretch 之前插入卡片
        stretch_index = target_row.count() - 1
        if stretch_index >= 0:
            target_row.insertWidget(stretch_index, card)
        else:
            target_row.addWidget(card)

    def clear(self) -> None:
        """清空所有行。"""
        for row in self._rows:
            # 移除行内所有控件
            while row.count():
                item = row.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
        self._rows.clear()
        # 从容器中移除行 layout
        while self._container.count():
            item = self._container.takeAt(0)
            sub = item.layout()
            if sub:
                while sub.count():
                    child = sub.takeAt(0)
                    w = child.widget()
                    if w:
                        w.deleteLater()


# ===================================================================
# ToolLibraryView
# ===================================================================
class ToolLibraryView(QWidget):
    """
    工具库区主视图。

    用法（在 MainWindow 中）：
        library = ToolLibraryView()
        library.tool_launch_requested.connect(on_launch)
        library.refresh()  # 首次加载
    """

    tool_launch_requested = Signal(str)  # tool_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow_grid: _FlowGridLayout | None = None
        self._empty_hint: QLabel | None = None
        self._search_input: QLineEdit | None = None
        # 保持对所有非模态弹窗的强引用，防止 GC 回收导致窗口消失
        self._active_dialogs: list[DynamicToolDialog] = []
        self._all_tools: dict[str, dict] = {}  # tool_id → {tool_name, description} 缓存，用于搜索过滤
        # 多选状态
        self._rendered_cards: dict[str, ToolCard] = {}
        self._selected_ids: set[str] = set()
        self._last_clicked_card_id: str | None = None
        # 框选状态
        self._rubber_band_origin: QPoint | None = None
        self._rubber_band: QRubberBand | None = None
        self._is_rubber_banding: bool = False
        # 批量操作栏
        self._batch_bar: QWidget | None = None
        self._batch_count_label: QLabel | None = None

        self._setup_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # 事件过滤器 — 拦截卡片容器上的鼠标事件实现框选 + Ctrl/Shift 多选
    # ------------------------------------------------------------------
    def eventFilter(self, watched, event: QEvent) -> bool:
        if watched is self._scroll_area.viewport():
            return self._handle_viewport_event(event)
        return super().eventFilter(watched, event)

    def _handle_viewport_event(self, event: QEvent) -> bool:
        """处理 scroll area viewport 上的鼠标事件。"""
        etype = event.type()

        if etype == QEvent.Type.MouseButtonPress:
            return self._on_viewport_press(event)
        elif etype == QEvent.Type.MouseMove and self._is_rubber_banding:
            return self._on_viewport_move(event)
        elif etype == QEvent.Type.MouseButtonRelease and self._is_rubber_banding:
            return self._on_viewport_release(event)

        return False

    def _on_viewport_press(self, event: QEvent) -> bool:
        """鼠标按下：判断是否要开始框选。"""
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        pos = event.position().toPoint()

        # 如果点击在卡片上 → 让卡片自行处理（mousePressEvent）
        hit_card = self._card_at(pos)
        if hit_card is not None:
            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+click：切换单张卡片选中状态
                hit_card.set_selected(not hit_card.is_selected)
                self._last_clicked_card_id = hit_card.tool_id
                return True
            elif modifiers & Qt.KeyboardModifier.ShiftModifier and self._last_clicked_card_id:
                # Shift+click：范围选择
                self._select_range(self._last_clicked_card_id, hit_card.tool_id)
                return True
            else:
                # 普通点击 → 取消全选，仅选中当前卡片
                self._clear_selection()
                hit_card.set_selected(True)
                self._last_clicked_card_id = hit_card.tool_id
                return True

        # 点击空白区域 → 开始框选
        self._clear_selection()
        self._last_clicked_card_id = None
        self._rubber_band_origin = pos
        if self._rubber_band is None:
            self._rubber_band = QRubberBand(
                QRubberBand.Shape.Rectangle, self._scroll_area.viewport()
            )
        self._rubber_band.setGeometry(QRect(pos, QPoint(pos.x() + 1, pos.y() + 1)))
        self._rubber_band.show()
        self._is_rubber_banding = True
        return True

    def _on_viewport_move(self, event: QEvent) -> bool:
        """鼠标移动：更新框选区域。"""
        if self._rubber_band and self._rubber_band_origin:
            rect = QRect(self._rubber_band_origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(rect)
        return True

    def _on_viewport_release(self, event: QEvent) -> bool:
        """鼠标释放：完成框选。"""
        self._is_rubber_banding = False
        if self._rubber_band:
            rubber_rect = self._rubber_band.geometry()
            self._rubber_band.hide()

            # 选中框内所有卡片
            for card in self._rendered_cards.values():
                # 将卡片坐标映射到 viewport 坐标
                card_rect = QRect(
                    card.mapTo(self._scroll_area.viewport(), QPoint(0, 0)),
                    card.size(),
                )
                if rubber_rect.intersects(card_rect):
                    card.set_selected(True)
        return True

    def _card_at(self, viewport_pos: QPoint) -> ToolCard | None:
        """返回 viewport 坐标下的卡片，无则返回 None。"""
        for card in self._rendered_cards.values():
            card_rect = QRect(
                card.mapTo(self._scroll_area.viewport(), QPoint(0, 0)),
                card.size(),
            )
            if card_rect.contains(viewport_pos):
                return card
        return None

    def _select_range(self, from_id: str, to_id: str) -> None:
        """Shift+click 范围选择：选中 from_id 到 to_id 之间所有卡片。"""
        card_ids = list(self._rendered_cards.keys())
        if from_id not in card_ids or to_id not in card_ids:
            return
        idx_from = card_ids.index(from_id)
        idx_to = card_ids.index(to_id)
        lo, hi = min(idx_from, idx_to), max(idx_from, idx_to)
        self._clear_selection()
        for i in range(lo, hi + 1):
            card = self._rendered_cards[card_ids[i]]
            card.set_selected(True)

    # ------------------------------------------------------------------
    # 多选管理
    # ------------------------------------------------------------------
    def _on_card_selection_toggled(self, tool_id: str, selected: bool) -> None:
        """卡片选中状态变化时更新内部集合并刷新批量操作栏。"""
        if selected:
            self._selected_ids.add(tool_id)
        else:
            self._selected_ids.discard(tool_id)
        self._update_batch_bar()

    def _clear_selection(self) -> None:
        """取消所有卡片的选中状态。"""
        for card in self._rendered_cards.values():
            card.set_selected(False)
        self._selected_ids.clear()
        self._update_batch_bar()

    def _update_batch_bar(self) -> None:
        """根据选中数量显示/隐藏批量操作栏。"""
        count = len(self._selected_ids)
        if count >= 1:
            if self._batch_bar is None:
                self._create_batch_bar()
            if self._batch_count_label:
                self._batch_count_label.setText(f"已选 {count} 个工具")
            self._batch_bar.setVisible(True)
        else:
            if self._batch_bar:
                self._batch_bar.setVisible(False)

    def _create_batch_bar(self) -> None:
        """创建批量操作栏（浮动在卡片区上方）。"""
        bar = QWidget(self)
        bar.setObjectName("batch_bar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(8)

        count_lbl = QLabel(f"已选 0 个工具")
        count_lbl.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        count_lbl.setStyleSheet("color: #1565C0;")
        bar_layout.addWidget(count_lbl)
        self._batch_count_label = count_lbl

        bar_layout.addStretch()

        delete_btn = QPushButton("🗑️ 批量删除")
        delete_btn.setFont(QFont("Microsoft YaHei", 10))
        delete_btn.setStyleSheet(
            "QPushButton { background: #EF5350; color: white; border-radius: 6px; "
            "padding: 4px 16px; font-weight: bold; } "
            "QPushButton:hover { background: #E53935; }"
        )
        delete_btn.clicked.connect(self._on_batch_delete)
        bar_layout.addWidget(delete_btn)

        deselect_btn = QPushButton("取消选择")
        deselect_btn.setFont(QFont("Microsoft YaHei", 10))
        deselect_btn.setStyleSheet(
            "QPushButton { border: 1px solid #CCC; border-radius: 6px; "
            "padding: 4px 16px; background: white; } "
            "QPushButton:hover { background: #F5F5F5; }"
        )
        deselect_btn.clicked.connect(self._clear_selection)
        bar_layout.addWidget(deselect_btn)

        bar.setStyleSheet(
            "QWidget#batch_bar { background: #E3F2FD; border-bottom: 1px solid #BBDEFB; }"
        )

        # 插入到卡片容器之前
        if self._scroll_area and self._scroll_area.widget():
            container_layout = self._scroll_area.widget().layout()
            if isinstance(container_layout, QVBoxLayout):
                container_layout.insertWidget(0, bar)
        self._batch_bar = bar

    def _on_batch_delete(self) -> None:
        """批量删除选中的工具。"""
        count = len(self._selected_ids)
        if count == 0:
            return

        reply = QMessageBox.question(
            self,
            "确认批量删除",
            f"确定要删除选中的 {count} 个工具吗？\n\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for tool_id in list(self._selected_ids):
            tool_dir = TOOLS_DIR / tool_id
            if tool_dir.exists():
                try:
                    shutil.rmtree(tool_dir)
                    logger.info("批量删除工具目录 | path=%s", tool_dir)
                except OSError as e:
                    logger.error("批量删除失败 | path=%s | error=%s", tool_dir, e)

        # 从索引中移除
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                for tool_id in list(self._selected_ids):
                    index_data.get("tools", {}).pop(tool_id, None)
                TOOLS_INDEX.write_text(
                    json.dumps(index_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info("批量从索引移除 %d 个工具", count)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("批量更新索引失败 | error=%s", e)

        self._selected_ids.clear()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        """初始化完整布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---- 标题栏 ----
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel("📦 工具库")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #333;")
        header.addWidget(title)

        header.addStretch()

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setFont(QFont("Microsoft YaHei", 10))
        refresh_btn.setStyleSheet(
            "QPushButton { border: 1px solid #CCC; border-radius: 6px; "
            "padding: 4px 16px; background: white; } "
            "QPushButton:hover { background: #F5F5F5; }"
        )
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)

        root.addLayout(header)

        # ---- 搜索栏 ----
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 搜索工具（按名称或描述过滤）...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFont(QFont("Microsoft YaHei", 10))
        self._search_input.setMinimumHeight(32)
        self._search_input.setStyleSheet(
            """
            QLineEdit {
                border: 1px solid #CCC;
                border-radius: 8px;
                padding: 4px 12px;
                background: white;
            }
            QLineEdit:focus {
                border: 1px solid #1976D2;
            }
            """
        )
        self._search_input.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search_input)

        # ---- 空状态提示 ----
        self._empty_hint = QLabel(
            "📭 还没有任何工具\n\n"
            "切换到「对话创造区」，用自然语言描述需求，\n"
            "AI 将自动为您生成专属小工具！"
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setFont(QFont("Microsoft YaHei", 12))
        self._empty_hint.setStyleSheet("color: #999; line-height: 1.8; margin: 60px 0px;")
        root.addWidget(self._empty_hint)

        # ---- 卡片滚动区 ----
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        card_container = QWidget()
        card_container.setObjectName("card_container")
        card_container.setStyleSheet("background: transparent;")
        card_layout = QVBoxLayout(card_container)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(14)

        self._flow_grid = _FlowGridLayout(card_layout, card_width=ToolCard.CARD_WIDTH, spacing=16)

        # 安装事件过滤器 — 实现框选 + Ctrl/Shift 多选
        self._scroll_area.viewport().installEventFilter(self)

        self._scroll_area.setWidget(card_container)
        root.addWidget(self._scroll_area, 1)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """
        重新加载 tools_config.json 并重建卡片网格。

        如果索引文件不存在或为空，显示空状态提示。
        """
        if self._flow_grid is None:
            return

        # 清空已有卡片
        self._flow_grid.clear()

        # ---- 读取索引 ----
        if not TOOLS_INDEX.exists():
            self._show_empty_state(True)
            return

        try:
            index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"读取 tools_config.json 失败: {e}")
            self._show_empty_state(True)
            return

        tools = index_data.get("tools", {})
        if not tools:
            self._show_empty_state(True)
            self._all_tools = {}
            return

        self._show_empty_state(False)

        # 缓存全部工具数据（用于搜索过滤）
        self._all_tools = {
            tid: {
                "tool_name": info.get("tool_name", "未命名工具"),
                "description": info.get("description", ""),
            }
            for tid, info in tools.items()
        }

        # 应用当前搜索词过滤
        search_text = self._search_input.text().strip() if self._search_input else ""
        self._render_cards(tools, search_text)

        logger.info(f"工具库已加载 {len(tools)} 个工具")

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_card_launch(self, tool_id: str) -> None:
        """
        卡片【启动】按钮槽：读取 config.json + script.py → 弹出 DynamicToolDialog。

        流程：
            1. 读取 tools/{tool_id}/config.json → 获取 meta + ui
            2. 定位 tools/{tool_id}/script.py
            3. 实例化 DynamicToolDialog（非模态）
        """
        config_path = TOOLS_DIR / tool_id / "config.json"
        script_path = TOOLS_DIR / tool_id / "script.py"

        if not config_path.exists():
            QMessageBox.warning(self, "加载失败", f"工具配置文件不存在:\n{config_path}")
            return
        if not script_path.exists():
            QMessageBox.warning(self, "加载失败", f"工具脚本文件不存在:\n{script_path}")
            return

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.critical(self, "解析错误", f"无法解析工具配置:\n{e}")
            return

        meta = config.get("meta", {})
        ui_schema = config.get("ui", [])

        tool_name = meta.get("tool_name", "未命名工具")
        description = meta.get("description", "")

        # 实例化非模态执行弹窗
        logger.info(
            "正在启动工具弹窗 | tool_id=%s | name=%s | ui_count=%d",
            tool_id, tool_name, len(ui_schema),
        )
        dialog = DynamicToolDialog(
            tool_id=tool_id,
            tool_name=tool_name,
            description=description,
            ui_schema=ui_schema,
            script_path=str(script_path),
            parent=None,
        )
        # 保持强引用，防止 GC 回收导致窗口消失
        self._active_dialogs.append(dialog)
        # 弹窗关闭时从列表中移除
        dialog.destroyed.connect(
            lambda d=dialog: (
                self._active_dialogs.remove(d) if d in self._active_dialogs else None
            )
        )
        dialog.show()
        logger.info(
            "工具弹窗已显示 | tool_id=%s | geometry=%s", tool_id, dialog.geometry()
        )

        # 同时通过信号通知全局（可选）
        self.tool_launch_requested.emit(tool_id)

    def _on_card_copy_prompt(self, tool_id: str) -> None:
        """
        卡片【复制提示词】按钮槽：生成结构化微调提示词模板 → 写入剪贴板。

        提示词包含：
            1. 用户创建该工具时的原始自然语言需求（从 tools_config.json 读取）
            2. 工具名称、描述、UI 参数清单
            3. 完整脚本代码
            4. 微调引导指令
        """
        config_path = TOOLS_DIR / tool_id / "config.json"
        script_path = TOOLS_DIR / tool_id / "script.py"

        if not config_path.exists() or not script_path.exists():
            QMessageBox.warning(self, "复制失败", "工具配置文件或脚本不存在，无法生成提示词。")
            return

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            script_code = script_path.read_text(encoding="utf-8")
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.critical(self, "读取错误", f"无法读取工具数据:\n{e}")
            return

        # ---- 从索引中读取用户原始提示词 ----
        original_prompt = ""
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                tool_info = index_data.get("tools", {}).get(tool_id, {})
                original_prompt = tool_info.get("user_prompt", "")
            except (json.JSONDecodeError, OSError):
                pass  # 索引读取出错不阻断主流程

        meta = config.get("meta", {})
        ui_schema = config.get("ui", [])

        tool_name = meta.get("tool_name", "未命名工具")
        description = meta.get("description", "")

        # 构建 UI 参数清单（人类可读）
        ui_lines = []
        for item in ui_schema:
            item_id = item.get("id", "?")
            item_type = item.get("type", "?")
            item_label = item.get("label", item_id)
            item_default = item.get("default", "")
            options = item.get("options", [])

            type_desc = {
                "folder_picker": "文件夹选择器",
                "file_picker": "文件选择器",
                "text_input": "文本输入框",
                "dropdown": "下拉菜单",
                "checkbox": "复选框",
                "spinbox": "数字输入框",
            }.get(item_type, item_type)

            line = f"  - {item_label} (ID: {item_id}, 类型: {type_desc})"
            if item_default:
                line += f" [默认值: {item_default}]"
            if options:
                line += f" [可选值: {', '.join(options)}]"
            ui_lines.append(line)

        ui_desc = "\n".join(ui_lines) if ui_lines else "（无额外输入参数）"

        # ---- 拼装提示词 ----
        prompt_parts = [f"【微调提示词】{tool_name}", ""]

        if original_prompt.strip():
            prompt_parts.append(f"- 您的原始需求：{original_prompt}")
            prompt_parts.append("")

        prompt_parts.extend([
            f"- 工具名称：{tool_name}",
            f"- 功能描述：{description}",
            "",
            "- 当前 UI 参数：",
            ui_desc,
            "",
            "- 当前执行脚本：",
            "```python",
            script_code,
            "```",
            "",
            "- 修改需求（请在此描述您的微调需求）：",
            "  - 例：修改默认参数值 / 新增输入项 / 调整输出逻辑 / 修复边界条件 / 增加错误处理",
        ])

        prompt = "\n".join(prompt_parts).strip()

        # 写入系统剪贴板
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(prompt, QClipboard.Mode.Clipboard)
            logger.info("提示词已复制到剪贴板 | tool_id=%s | tool_name=%s", tool_id, tool_name)
            QMessageBox.information(self, "已复制", f"「{tool_name}」的微调提示词已复制到剪贴板。\n您可以粘贴到对话创造区进行微调。")
        else:
            QMessageBox.warning(self, "复制失败", "无法访问系统剪贴板。")

    # ------------------------------------------------------------------
    # 槽函数（续）
    # ------------------------------------------------------------------
    def _on_card_delete(self, tool_id: str) -> None:
        """
        卡片【删除】按钮槽：确认后删除工具目录 + 从索引移除，然后刷新视图。

        删除操作：
            1. 弹确认框
            2. 删除 tools/{tool_id}/ 整个目录
            3. 从 tools_config.json 索引中移除该条目
            4. 刷新卡片网格
        """
        # 读取工具名称用于确认提示
        tool_name = tool_id
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                tool_info = index_data.get("tools", {}).get(tool_id, {})
                tool_name = tool_info.get("tool_name", tool_id)
            except (json.JSONDecodeError, OSError):
                pass

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除工具「{tool_name}」吗？\n\n"
            "此操作将移除该工具的所有配置与脚本文件，不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # 删除工具目录
        tool_dir = TOOLS_DIR / tool_id
        if tool_dir.exists():
            try:
                shutil.rmtree(tool_dir)
                logger.info("已删除工具目录 | path=%s", tool_dir)
            except OSError as e:
                logger.error("删除工具目录失败 | path=%s | error=%s", tool_dir, e)
                QMessageBox.critical(self, "删除失败", f"无法删除工具目录:\n{e}")
                return

        # 从索引中移除
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                removed = index_data.get("tools", {}).pop(tool_id, None)
                if removed is not None:
                    TOOLS_INDEX.write_text(
                        json.dumps(index_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info("已从索引移除工具 | tool_id=%s | tool_name=%s", tool_id, tool_name)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("更新索引失败 | error=%s", e)
                QMessageBox.warning(self, "删除完成", f"工具目录已删除，但索引更新失败:\n{e}")

        # 刷新视图
        self.refresh()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _show_empty_state(self, show: bool) -> None:
        """切换空状态提示与卡片滚动区的可见性。"""
        if self._empty_hint:
            self._empty_hint.setVisible(show)
        if self._scroll_area:
            self._scroll_area.setVisible(not show)

    def _render_cards(self, tools: dict, search_text: str) -> None:
        """
        根据搜索词过滤并渲染卡片。

        过滤逻辑：search_text 为空 → 全部展示
                  否则 → 工具名称或描述包含 search_text（忽略大小写）
        """
        if self._flow_grid is None:
            return

        self._flow_grid.clear()
        self._rendered_cards.clear()
        self._selected_ids.clear()
        self._update_batch_bar()

        search_lower = search_text.lower()
        filtered = 0

        for tool_id, info in tools.items():
            tool_name = info.get("tool_name", "未命名工具")
            description = info.get("description", "")

            if search_lower:
                if search_lower not in tool_name.lower() and search_lower not in description.lower():
                    continue

            filtered += 1
            card = ToolCard(
                tool_id=tool_id,
                tool_name=tool_name,
                description=description,
            )
            card.launch_requested.connect(self._on_card_launch)
            card.copy_prompt_requested.connect(self._on_card_copy_prompt)
            card.rename_requested.connect(self._on_card_rename)
            card.delete_requested.connect(self._on_card_delete)
            card.selection_toggled.connect(self._on_card_selection_toggled)
            self._rendered_cards[tool_id] = card
            self._flow_grid.add_card(card)

        if filtered == 0 and search_text:
            self._show_empty_state(True)
            if self._empty_hint:
                self._empty_hint.setText(
                    f"🔍 没有找到匹配「{search_text}」的工具\n\n"
                    "请尝试更换搜索关键词"
                )
            logger.info("搜索无结果 | search=%s", search_text)
        else:
            self._show_empty_state(False)
            logger.info("已渲染 %d 张卡片 | search=%s", filtered, search_text or "（全部）")

    # ------------------------------------------------------------------
    # 槽函数（搜索 & 重命名）
    # ------------------------------------------------------------------
    def _on_search_changed(self, text: str) -> None:
        """搜索文本变化时实时过滤卡片。"""
        search_text = text.strip()
        self._render_cards(self._all_tools, search_text)

    def _on_card_rename(self, tool_id: str) -> None:
        """
        卡片【重命名】按钮槽：弹输入框 → 更新索引 + config.json → 刷新。

        更新范围：
            1. tools_config.json 索引中的 tool_name
            2. tools/{tool_id}/config.json 中的 meta.tool_name
            3. 刷新卡片网格（更新显示、更新搜索缓存）
        """
        # 获取当前名称
        old_name = tool_id
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                tool_info = index_data.get("tools", {}).get(tool_id, {})
                old_name = tool_info.get("tool_name", tool_id)
            except (json.JSONDecodeError, OSError):
                pass

        new_name, ok = QInputDialog.getText(
            self,
            "重命名工具",
            f"请输入新名称（当前：{old_name}）：",
            text=old_name,
        )

        if not ok or not new_name.strip():
            return  # 用户取消或输入为空

        new_name = new_name.strip()
        if new_name == old_name:
            return  # 名称未变化

        # ---- 更新 tools_config.json 索引 ----
        index_updated = False
        if TOOLS_INDEX.exists():
            try:
                index_data = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
                if tool_id in index_data.get("tools", {}):
                    index_data["tools"][tool_id]["tool_name"] = new_name
                    TOOLS_INDEX.write_text(
                        json.dumps(index_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    index_updated = True
                    logger.info(
                        "索引已更新 | tool_id=%s | old=%s | new=%s",
                        tool_id, old_name, new_name,
                    )
            except (json.JSONDecodeError, OSError) as e:
                logger.error("更新索引失败 | error=%s", e)
                QMessageBox.warning(self, "重命名失败", f"索引更新失败:\n{e}")
                return

        # ---- 更新 tools/{tool_id}/config.json ----
        config_path = TOOLS_DIR / tool_id / "config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if "meta" in config:
                    config["meta"]["tool_name"] = new_name
                    config_path.write_text(
                        json.dumps(config, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info("config.json 已更新 | tool_id=%s", tool_id)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("更新 config.json 失败 | error=%s", e)
                QMessageBox.warning(
                    self, "部分更新",
                    f"索引已更新为「{new_name}」，但 config.json 更新失败:\n{e}",
                )
                # 索引已更新，继续刷新视图

        # 刷新视图
        self.refresh()
