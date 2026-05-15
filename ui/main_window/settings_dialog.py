"""
SettingsDialog — API 配置修改对话框

支持在主界面中随时修改 LLM API 配置（provider、base_url、api_key、model 等），
保存后直接写入 anybox_config.json 并通知调用方更新 LLM 控制器。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ui.wizard.api_purchase_guide import APIPurchaseGuideDialog, SETTINGS_PROVIDERS

# ---------------------------------------------------------------------------
# 路径常量
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
CONFIG_FILE = ROOT_DIR / "anybox_config.json"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logger = logging.getLogger("anybox.settings_dialog")

# ---------------------------------------------------------------------------
# Provider 预设
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, str | int | float]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "max_tokens": 16384,
        "temperature": 0.7,
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "custom": {
        "base_url": "",
        "model": "",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
}


# ===================================================================
# SettingsDialog
# ===================================================================
class SettingsDialog(QDialog):
    """
    API 配置修改对话框。

    Signals:
        config_updated(dict) — 保存后发射，携带新的完整配置字典
    """

    config_updated = Signal(dict)

    def __init__(self, current_config: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚙️ API 配置")
        self.setMinimumWidth(480)
        self.setModal(True)

        self._current_config = current_config or {}
        self._build_ui()
        self._load_config()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """构建表单界面。"""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ---- 标题 ----
        title = QLabel("LLM API 配置")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #333;")
        layout.addWidget(title)

        desc = QLabel("修改后点击保存，配置将立即生效（对话历史会清空）。")
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei", 9))
        desc.setStyleSheet("color: #888; margin-bottom: 8px;")
        layout.addWidget(desc)

        # ---- 分隔线 ----
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #E0E0E0;")
        layout.addWidget(line)

        # ---- 表单 ----
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Provider
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["deepseek", "kimi", "qwen", "zhipu", "openai", "custom"])
        self._provider_combo.setFont(QFont("Microsoft YaHei", 10))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("Provider:", self._provider_combo)

        # Base URL
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self._base_url_edit.setFont(QFont("Microsoft YaHei", 10))
        self._base_url_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #CCC; border-radius: 4px; padding: 6px 8px; }"
            "QLineEdit:focus { border-color: #1976D2; }"
        )
        form.addRow("Base URL:", self._base_url_edit)

        # API Key
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-...")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setFont(QFont("Microsoft YaHei", 10))
        self._api_key_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #CCC; border-radius: 4px; padding: 6px 8px; }"
            "QLineEdit:focus { border-color: #1976D2; }"
        )

        # 显示/隐藏 API Key 切换
        key_row = QHBoxLayout()
        key_row.setSpacing(4)
        key_row.addWidget(self._api_key_edit, 1)
        self._toggle_key_btn = QPushButton("👁")
        self._toggle_key_btn.setFixedSize(32, 32)
        self._toggle_key_btn.setToolTip("显示/隐藏 API Key")
        self._toggle_key_btn.setStyleSheet(
            "QPushButton { border: 1px solid #CCC; border-radius: 4px; background: #F5F5F5; }"
            "QPushButton:hover { background: #E0E0E0; }"
        )
        self._toggle_key_btn.clicked.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self._toggle_key_btn)
        form.addRow("API Key:", key_row)

        # Model
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("gpt-4o")
        self._model_edit.setFont(QFont("Microsoft YaHei", 10))
        self._model_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #CCC; border-radius: 4px; padding: 6px 8px; }"
            "QLineEdit:focus { border-color: #1976D2; }"
        )
        form.addRow("Model:", self._model_edit)

        # Max Tokens
        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(256, 128000)
        self._max_tokens_spin.setSingleStep(256)
        self._max_tokens_spin.setValue(4096)
        self._max_tokens_spin.setFont(QFont("Microsoft YaHei", 10))
        self._max_tokens_spin.setStyleSheet(
            "QSpinBox { border: 1px solid #CCC; border-radius: 4px; padding: 4px 6px; }"
            "QSpinBox:focus { border-color: #1976D2; }"
        )
        form.addRow("Max Tokens:", self._max_tokens_spin)

        # Temperature
        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.1)
        self._temp_spin.setDecimals(2)
        self._temp_spin.setValue(0.7)
        self._temp_spin.setFont(QFont("Microsoft YaHei", 10))
        self._temp_spin.setStyleSheet(
            "QDoubleSpinBox { border: 1px solid #CCC; border-radius: 4px; padding: 4px 6px; }"
            "QDoubleSpinBox:focus { border-color: #1976D2; }"
        )
        form.addRow("Temperature:", self._temp_spin)

        # ---- 思考模式 (DeepSeek v4) ----
        thinking_box = QGroupBox("🧠 思考模式 (DeepSeek v4)")
        thinking_box.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #DDD; "
            "border-radius: 6px; margin-top: 8px; padding-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        thinking_layout = QVBoxLayout(thinking_box)
        thinking_layout.setSpacing(8)

        self._thinking_check = QCheckBox("启用思考模式")
        self._thinking_check.setToolTip(
            "DeepSeek v4 模型会在输出最终回答前先生成思维链，提升答案准确性。\n"
            "启用时 temperature/top_p 等参数不会生效。"
        )
        self._thinking_check.setChecked(True)
        self._thinking_check.toggled.connect(self._on_thinking_toggled)
        thinking_layout.addWidget(self._thinking_check)

        effort_row = QHBoxLayout()
        effort_row.setSpacing(8)
        effort_label = QLabel("思考强度：")
        effort_label.setToolTip("high = 标准推理 | max = 深度推理（复杂任务推荐）")
        effort_row.addWidget(effort_label)

        self._effort_combo = QComboBox()
        self._effort_combo.addItems(["high", "max"])
        self._effort_combo.setFont(QFont("Microsoft YaHei", 10))
        self._effort_combo.setStyleSheet(
            "QComboBox { border: 1px solid #CCC; border-radius: 4px; padding: 4px 6px; }"
            "QComboBox:focus { border-color: #1976D2; }"
        )
        effort_row.addWidget(self._effort_combo)
        effort_row.addStretch()
        thinking_layout.addLayout(effort_row)

        thinking_hint = QLabel(
            "思考模式下 temperature 等采样参数不会生效。\n"
            "仅 DeepSeek v4 系列模型支持此功能。"
        )
        thinking_hint.setWordWrap(True)
        thinking_hint.setStyleSheet("color: #999; font-size: 9pt;")
        thinking_layout.addWidget(thinking_hint)

        layout.addWidget(thinking_box)

        layout.addLayout(form)

        # ---- "如何购买API" 按钮 ----
        help_row = QHBoxLayout()
        help_row.addStretch()
        help_btn = QPushButton("🛒 如何购买API？")
        help_btn.setFont(QFont("Microsoft YaHei", 9))
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #1976D2; "
            "border: 1px solid #1976D2; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #E3F2FD; }"
        )
        help_btn.clicked.connect(self._on_show_purchase_guide)
        help_row.addWidget(help_btn)
        layout.addLayout(help_row)

        # ---- 底部按钮 ----
        layout.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFont(QFont("Microsoft YaHei", 10))
        cancel_btn.setStyleSheet(
            "QPushButton { padding: 8px 24px; border: 1px solid #CCC; "
            "border-radius: 6px; background: #F5F5F5; }"
            "QPushButton:hover { background: #E0E0E0; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("💾 保存")
        save_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        save_btn.setStyleSheet(
            "QPushButton { padding: 8px 24px; border: none; border-radius: 6px; "
            "background: #1976D2; color: white; }"
            "QPushButton:hover { background: #1565C0; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        """从配置文件或内存加载当前配置。"""
        # 优先从文件加载以确保是最新的
        try:
            if CONFIG_FILE.exists():
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            else:
                config = self._current_config
        except (json.JSONDecodeError, OSError):
            config = self._current_config

        provider = config.get("provider", "openai")
        base_url = config.get("base_url", "")
        api_key = config.get("api_key", "")
        model = config.get("model", "")
        max_tokens = config.get("max_tokens", 4096)
        temperature = config.get("temperature", 0.7)

        # Provider
        idx = self._provider_combo.findText(provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        else:
            self._provider_combo.setCurrentText("custom")

        # 如果 provider 匹配预设且字段为空，使用预设值
        if provider in PROVIDER_PRESETS and provider != "custom":
            preset = PROVIDER_PRESETS[provider]
            if not base_url:
                base_url = str(preset["base_url"])
            if not model:
                model = str(preset["model"])

        self._base_url_edit.setText(base_url)
        self._api_key_edit.setText(api_key)
        self._model_edit.setText(model)
        self._max_tokens_spin.setValue(int(max_tokens))
        self._temp_spin.setValue(float(temperature))

        # 思考模式
        thinking = config.get("thinking", True)
        reasoning_effort = config.get("reasoning_effort", "high")
        self._thinking_check.setChecked(thinking)
        idx_effort = self._effort_combo.findText(reasoning_effort)
        if idx_effort >= 0:
            self._effort_combo.setCurrentIndex(idx_effort)
        self._effort_combo.setEnabled(thinking)

    # ------------------------------------------------------------------
    def _on_provider_changed(self, provider: str) -> None:
        """Provider 切换时自动填充预设值。"""
        if provider == "custom":
            return  # 不清空用户已填内容
        preset = PROVIDER_PRESETS.get(provider)
        if preset:
            self._base_url_edit.setText(str(preset["base_url"]))
            self._model_edit.setText(str(preset["model"]))
            self._max_tokens_spin.setValue(int(preset["max_tokens"]))
            self._temp_spin.setValue(float(preset["temperature"]))

    # ------------------------------------------------------------------
    def _on_show_purchase_guide(self) -> None:
        """弹出「如何购买 API」引导窗口。"""
        dlg = APIPurchaseGuideDialog(
            provider_keys=SETTINGS_PROVIDERS,
            parent=self,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    def _toggle_api_key_visibility(self) -> None:
        """切换 API Key 的明文/密码模式。"""
        if self._api_key_edit.echoMode() == QLineEdit.EchoMode.Password:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_key_btn.setText("🙈")
        else:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_key_btn.setText("👁")

    # ------------------------------------------------------------------
    def _on_thinking_toggled(self, checked: bool) -> None:
        """思考模式切换时，启用/禁用思考强度选择。"""
        self._effort_combo.setEnabled(checked)

    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        """校验并保存配置到 anybox_config.json。"""
        provider = self._provider_combo.currentText()
        base_url = self._base_url_edit.text().strip()
        api_key = self._api_key_edit.text().strip()
        model = self._model_edit.text().strip()
        max_tokens = self._max_tokens_spin.value()
        temperature = self._temp_spin.value()
        thinking = self._thinking_check.isChecked()
        reasoning_effort = self._effort_combo.currentText()

        # 基本校验
        if not api_key:
            QMessageBox.warning(self, "校验失败", "API Key 不能为空。")
            self._api_key_edit.setFocus()
            return
        if not base_url:
            QMessageBox.warning(self, "校验失败", "Base URL 不能为空。")
            self._base_url_edit.setFocus()
            return
        if not model:
            QMessageBox.warning(self, "校验失败", "Model 不能为空。")
            self._model_edit.setFocus()
            return

        new_config: dict = {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": thinking,
            "reasoning_effort": reasoning_effort,
        }

        # 写入配置文件
        try:
            CONFIG_FILE.write_text(
                json.dumps(new_config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("API 配置已更新并写入 %s", CONFIG_FILE)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\n{e}")
            logger.error("保存配置失败: %s", e)
            return

        self.config_updated.emit(new_config)
        self.accept()
