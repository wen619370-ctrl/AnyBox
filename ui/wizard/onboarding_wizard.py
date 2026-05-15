"""
OnboardingWizard — 初始化向导

基于 QWizard 的三步配置流程：
    Page 1 (Welcome)      : 欢迎语
    Page 2 (API Setup)    : 服务商选择 + API Key 输入
    Page 3 (Test & Finish): 连通性测试，通过后允许完成

向导完成后：
    1. 将配置写入 ROOT_DIR/anybox_config.json
    2. 通过 signal_bus.onboarding_completed 通知 AnyBoxApp 切换至主窗口
"""

import json
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QWizard,
    QWizardPage,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QPushButton,
    QApplication,
    QDoubleSpinBox,
    QSpinBox,
    QSlider,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer, QUrl
from PySide6.QtGui import QFont, QIcon
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply, QSslSocket

from ui.wizard.api_purchase_guide import APIPurchaseGuideDialog, WIZARD_PROVIDERS

# ---------------------------------------------------------------------------
# 路径常量 — 与 main.py 同源
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


def _get_resource_dir() -> Path:
    """返回资源文件目录（assets 等）。

    开发环境：项目根目录。
    PyInstaller 打包后：_MEIPASS 临时解压目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


RESOURCE_DIR = _get_resource_dir()

# 需延迟导入以避免循环依赖
try:
    from main import signal_bus
except ImportError:
    signal_bus = None  # type: ignore

logger = logging.getLogger("anybox.wizard")

# ---------------------------------------------------------------------------
# 服务商预设
# ---------------------------------------------------------------------------
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "desc": "高性价比中文大模型",
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "desc": "长文本处理能力突出",
    },
    "qwen": {
        "name": "通义千问 (阿里云)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-turbo", "qwen-plus", "qwen-max"],
        "desc": "阿里云旗下大模型系列",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"],
        "desc": "清华智谱 AI 系列",
    },
    "custom": {
        "name": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "models": [],
        "desc": "适用于 Ollama、vLLM 等自部署服务",
    },
}


# ===================================================================
# 连通性测试 — 使用 Qt 原生网络栈 (QNetworkAccessManager)
#
# 设计考量：
#   - QNetworkAccessManager 基于 Qt 事件循环异步执行，不阻塞 UI。
#   - Qt 的网络栈不受 Windows 系统代理设置影响，彻底终结 urllib
#     在 GUI 进程中因代理配置而死锁的问题。
#   - 无需单独 QThread，代码复杂度大幅降低。
# ===================================================================
class _ConnectivityTester(QObject):
    """
    使用 QNetworkAccessManager 发送 GET /models 请求，
    不消耗 token，仅验证 api_key 有效性。

    用法：
        tester = _ConnectivityTester(base_url, api_key, parent)
        tester.test_finished.connect(on_result)
        tester.start()
    """

    test_finished = Signal(bool, str)  # (success, detail_message)

    _SANE_TIMEOUT_MS = 15_000  # 15 秒总超时

    def __init__(
        self,
        base_url: str,
        api_key: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

        # 每个测试实例拥有独立的 NetworkAccessManager，
        # 生命周期与页面绑定，页面切换时可自动清理。
        self._nam = QNetworkAccessManager(self)
        self._nam.setTransferTimeout(self._SANE_TIMEOUT_MS)

        self._reply: QNetworkReply | None = None

    def start(self) -> None:
        """发起异步 GET 请求。"""
        url = QUrl(f"{self._base_url}/models")
        req = QNetworkRequest(url)

        # ---- 请求头 ----
        req.setRawHeader(
            b"Authorization",
            f"Bearer {self._api_key}".encode("utf-8"),
        )
        req.setRawHeader(b"Accept", b"application/json")

        # ---- SSL 配置：容忍自签名/企业 CA（不影响安全性，仅用于连通性探活） ----
        ssl_config = req.sslConfiguration()
        ssl_config.setPeerVerifyMode(QSslSocket.PeerVerifyMode.VerifyNone)
        req.setSslConfiguration(ssl_config)

        self._reply = self._nam.get(req)
        self._reply.finished.connect(self._on_reply_finished)

        # 看门狗定时器（防止 QNetworkAccessManager 自身不超时的情况）
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_timeout)
        self._watchdog.start(self._SANE_TIMEOUT_MS + 2000)

    def abort(self) -> None:
        """安全中止进行中的请求。"""
        if self._reply is not None and self._reply.isRunning():
            self._reply.abort()
        self._cleanup()

    # ------------------------------------------------------------------
    def _on_reply_finished(self) -> None:
        """HTTP 请求完成（成功或失败均触发）。"""
        self._watchdog.stop()

        if self._reply is None:
            return

        error = self._reply.error()
        status = self._reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )

        if error == QNetworkReply.NetworkError.NoError and status == 200:
            self.test_finished.emit(True, "连接成功！API Key 验证通过。")
        elif status in (401, 403):
            self.test_finished.emit(
                False, f"API Key 无效或权限不足 (HTTP {status})。"
            )
        elif error == QNetworkReply.NetworkError.OperationCanceledError:
            self.test_finished.emit(
                False,
                "连接被中断，可能超时。\n"
                "请检查网络或点击「跳过测试」进入主程序。",
            )
        elif error == QNetworkReply.NetworkError.TimeoutError:
            self.test_finished.emit(
                False,
                "连接超时（15 秒）。\n"
                "若系统开启了 HTTP 代理，请关闭代理或「跳过测试」进入。",
            )
        else:
            detail = self._reply.errorString()
            self.test_finished.emit(
                False,
                f"网络请求失败 (HTTP {status}):\n{detail}\n\n"
                "提示：可直接「跳过测试」进入主程序。",
            )

        self._cleanup()

    def _on_timeout(self) -> None:
        """看门狗超时：强制终止请求。"""
        self.abort()
        self.test_finished.emit(
            False,
            "连接超时（超过 17 秒）。\n"
            "请检查网络或关闭系统代理后重试。\n"
            "也可以直接「跳过测试」进入主程序。",
        )

    def _cleanup(self) -> None:
        """释放网络资源。"""
        if self._reply is not None:
            self._reply.deleteLater()
            self._reply = None


# ===================================================================
# Page 1: Welcome
# ===================================================================
class _WelcomePage(QWizardPage):
    """欢迎页面：展示 AnyBox 简介。"""

    def __init__(self, parent: QWizard | None = None) -> None:
        super().__init__(parent)
        self.setTitle("欢迎使用 AnyBox · 万用沙盒")
        self.setSubTitle("三步完成初始化，即刻开启 AI 工具箱之旅。")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc = QLabel(
            "AnyBox 是一个「UI 渲染器 + Python 沙盒执行器」。\n\n"
            "您只需用自然语言描述需求，AI 将自动：\n"
            "  • 生成带有原生控件（文件选择器、下拉菜单等）的交互界面\n"
            "  • 编写在独立沙盒中运行的 Python 脚本\n"
            "  • 捕获执行结果并直接展示给您\n\n"
            "现在，请设置一个大模型 API 以便开始使用。"
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei", 11))
        desc.setStyleSheet("color: #444; line-height: 1.6;")
        layout.addWidget(desc)
        layout.addStretch()


# ===================================================================
# Page 2: API Setup
# ===================================================================
class _APISetupPage(QWizardPage):
    """API 配置页：服务商选择 + 模型名称 + API Key + 高级参数。"""

    def __init__(self, parent: QWizard | None = None) -> None:
        super().__init__(parent)
        self.setTitle("配置大模型 API")
        self.setSubTitle("选择模型服务商，填入 API Key，按需调整高级参数。")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ---- 服务商选择 ----
        provider_box = QGroupBox("选择服务商")
        provider_layout = QVBoxLayout(provider_box)
        provider_layout.setSpacing(6)

        self._btn_group = QButtonGroup(self)
        self._radio_map: dict[str, QRadioButton] = {}

        for key, info in PROVIDERS.items():
            radio = QRadioButton(f"{info['name']} — {info['desc']}")
            radio.setFont(QFont("Microsoft YaHei", 10))
            if key == "deepseek":
                radio.setChecked(True)
            provider_layout.addWidget(radio)
            self._btn_group.addButton(radio)
            self._radio_map[key] = radio

        self._btn_group.buttonClicked.connect(self._on_provider_changed)
        layout.addWidget(provider_box)

        # ---- API 端点 (Base URL) — 仅"自定义"时可见 ----
        self._url_label = QLabel("API 端点 (Base URL)：")
        self._url_label.setVisible(False)
        layout.addWidget(self._url_label)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("例如：http://localhost:11434/v1")
        self._url_edit.setVisible(False)
        self._url_edit.textChanged.connect(self.completeChanged)
        layout.addWidget(self._url_edit)

        # ---- 模型名称 ----
        model_label = QLabel("模型名称：")
        layout.addWidget(model_label)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.setFont(QFont("Microsoft YaHei", 10))
        self._model_combo.currentTextChanged.connect(self.completeChanged)
        layout.addWidget(self._model_combo)

        # ---- API Key ----
        key_label = QLabel("API Key：")
        layout.addWidget(key_label)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self._api_key_edit.textChanged.connect(self.completeChanged)
        layout.addWidget(self._api_key_edit)

        # ---- 高级参数 ----
        advanced_box = QGroupBox("高级参数（可选调整）")
        advanced_layout = QVBoxLayout(advanced_box)
        advanced_layout.setSpacing(8)

        # Temperature 行
        temp_row = QHBoxLayout()
        temp_lbl = QLabel("Temperature：")
        temp_lbl.setToolTip("控制输出随机性：0 = 确定，2 = 最大随机")
        temp_row.addWidget(temp_lbl)

        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.05)
        self._temp_spin.setValue(0.30)
        self._temp_spin.setDecimals(2)
        self._temp_spin.setFixedWidth(80)
        temp_row.addWidget(self._temp_spin)

        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 200)
        self._temp_slider.setValue(30)
        self._temp_spin.valueChanged.connect(self._sync_temp_to_slider)
        self._temp_slider.valueChanged.connect(self._sync_temp_to_spin)
        temp_row.addWidget(self._temp_slider, 1)
        advanced_layout.addLayout(temp_row)

        # Max Tokens 行
        token_row = QHBoxLayout()
        token_lbl = QLabel("Max Tokens：")
        token_lbl.setToolTip("单次生成的最大 token 数量")
        token_row.addWidget(token_lbl)

        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(256, 32768)
        self._max_tokens_spin.setSingleStep(512)
        self._max_tokens_spin.setValue(4096)
        self._max_tokens_spin.setFixedWidth(100)
        token_row.addWidget(self._max_tokens_spin)
        token_row.addStretch()
        advanced_layout.addLayout(token_row)

        layout.addWidget(advanced_box)

        # ---- 隐私提示 ----
        hint = QLabel(
            "您的 API Key 仅存储在本地配置文件 anybox_config.json 中，"
            "不会上传至任何第三方服务器。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #999; font-size: 9pt; margin-top: 6px;")
        layout.addWidget(hint)

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

        layout.addStretch()

        # 初始填充模型列表
        self._on_provider_changed(self._btn_group.checkedButton())

    # ------------------------------------------------------------------
    # Temperature 同步
    # ------------------------------------------------------------------
    def _sync_temp_to_slider(self, value: float) -> None:
        self._temp_slider.blockSignals(True)
        self._temp_slider.setValue(int(value * 100))
        self._temp_slider.blockSignals(False)

    def _sync_temp_to_spin(self, value: int) -> None:
        self._temp_spin.blockSignals(True)
        self._temp_spin.setValue(value / 100.0)
        self._temp_spin.blockSignals(False)

    # ------------------------------------------------------------------
    def _on_provider_changed(self, btn: QRadioButton | None) -> None:
        """服务商切换时：更新可选模型列表、控制自定义 URL 可见性。"""
        provider_key = self._selected_provider()
        provider = PROVIDERS[provider_key]
        is_custom = provider_key == "custom"

        # 更新模型下拉选项
        current = self._model_combo.currentText()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if is_custom:
            self._model_combo.setEditText(current if current else "")
        else:
            self._model_combo.addItems(provider.get("models", []))
            if current and current in provider.get("models", []):
                self._model_combo.setCurrentText(current)
        self._model_combo.blockSignals(False)

        # 自定义 URL 可见性
        self._url_label.setVisible(is_custom)
        self._url_edit.setVisible(is_custom)
        if not is_custom:
            self._url_edit.clear()

        self.completeChanged.emit()

    # ------------------------------------------------------------------
    def _on_show_purchase_guide(self) -> None:
        """弹出「如何购买 API」引导窗口。"""
        dlg = APIPurchaseGuideDialog(
            provider_keys=WIZARD_PROVIDERS,
            parent=self,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    def isComplete(self) -> bool:
        if not self._api_key_edit.text().strip():
            return False
        if not self._model_combo.currentText().strip():
            return False
        if self._selected_provider() == "custom" and not self._url_edit.text().strip():
            return False
        return True

    def _selected_provider(self) -> str:
        checked = self._btn_group.checkedButton()
        if checked is None:
            return "deepseek"
        for key, radio in self._radio_map.items():
            if radio is checked:
                return key
        return "deepseek"

    def collect_config(self) -> dict:
        provider_key = self._selected_provider()
        provider = PROVIDERS[provider_key]

        if provider_key == "custom":
            base_url = self._url_edit.text().strip().rstrip("/")
        else:
            base_url = provider["base_url"]

        return {
            "provider": provider_key,
            "base_url": base_url,
            "api_key": self._api_key_edit.text().strip(),
            "model": self._model_combo.currentText().strip(),
            "temperature": round(self._temp_spin.value(), 2),
            "max_tokens": self._max_tokens_spin.value(),
        }


# ===================================================================
# Page 3: Test & Finish
# ===================================================================
class _TestFinishPage(QWizardPage):
    """
    连通性测试页。

    铁律：只有测试通过后，Wizard 的【完成】按钮才可点击。
    """

    def __init__(self, parent: QWizard | None = None) -> None:
        super().__init__(parent)
        self.setTitle("测试连接")
        self.setSubTitle("点击按钮验证 API 连通性。仅测试通过后才可完成配置。")

        self._test_passed = False
        self._tester: _ConnectivityTester | None = None
        self._watchdog: QTimer | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ---- 信息汇总 ----
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(
            "background: #F5F5F5; border-radius: 6px; padding: 10px; "
            "color: #333; font-size: 10pt;"
        )
        layout.addWidget(self._summary_label)

        # ---- 测试按钮 ----
        btn_row = QHBoxLayout()
        self._test_btn = QPushButton("🔌 测试连接")
        self._test_btn.setStyleSheet(
            "QPushButton { padding: 8px 24px; font-weight: bold; "
            "background: #FF9800; color: white; border-radius: 6px; } "
            "QPushButton:hover { background: #F57C00; } "
            "QPushButton:disabled { background: #CCC; color: #888; }"
        )
        self._test_btn.clicked.connect(self._on_test_clicked)
        btn_row.addStretch()
        btn_row.addWidget(self._test_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ---- 跳过测试链接 ----
        self._skip_label = QLabel(
            '<a href="#" style="color: #999;">跳过测试，直接完成配置</a>'
        )
        self._skip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._skip_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_label.linkActivated.connect(self._on_skip_test)
        layout.addWidget(self._skip_label)

        # ---- 测试结果 ----
        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        self._result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_label.setStyleSheet("font-size: 12pt; min-height: 40px;")
        self._result_label.setVisible(False)
        layout.addWidget(self._result_label)

        layout.addStretch()

    # ------------------------------------------------------------------
    def cleanupPage(self) -> None:
        """用户离开本页时，终止进行中的后台测试以防资源泄漏。"""
        self._abort_test()

    # ------------------------------------------------------------------
    def _abort_test(self) -> None:
        """安全终止后台网络测试。"""
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.deleteLater()
            self._watchdog = None
        if self._tester is not None:
            self._tester.abort()
            self._tester.deleteLater()
            self._tester = None

    # ------------------------------------------------------------------
    def initializePage(self) -> None:
        """页面显示时，从 Page 2 读取配置并展示汇总。"""
        setup_page: _APISetupPage = self.wizard().page(1)  # type: ignore
        config = setup_page.collect_config()

        self._summary_label.setText(
            f"<b>服务商：</b>{PROVIDERS[config['provider']]['name']}<br>"
            f"<b>端点：</b>{config['base_url']}<br>"
            f"<b>模型：</b>{config.get('model', '—')}<br>"
            f"<b>API Key：</b>{config['api_key'][:8]}{'*' * (len(config['api_key']) - 8) if len(config['api_key']) > 8 else '***'}<br>"
            f"<b>Temperature：</b>{config.get('temperature', 0.3)}<br>"
            f"<b>Max Tokens：</b>{config.get('max_tokens', 4096)}"
        )

        # 存储 config 到 wizard 实例变量，供测试 Worker 和完成时读取
        # (不使用 setProperty，避免 QVariant 序列化相关问题)
        wizard = self.wizard()
        wizard._collected_config = config

    def isComplete(self) -> bool:
        """仅当测试通过后才允许完成。"""
        return self._test_passed

    # ------------------------------------------------------------------
    def _on_test_clicked(self) -> None:
        """启动后台连通性测试（Qt 原生异步，不阻塞 UI）。"""
        # 先终止上一次未完成的测试
        self._abort_test()

        self._test_btn.setEnabled(False)
        self._test_btn.setText("⏳ 测试中...")
        self._result_label.setVisible(True)
        self._result_label.setText("正在连接，请稍候...")
        self._result_label.setStyleSheet("color: #999; font-size: 11pt;")

        config = getattr(self.wizard(), "_collected_config", {})

        self._tester = _ConnectivityTester(
            base_url=config.get("base_url", ""),
            api_key=config.get("api_key", ""),
            parent=self,
        )
        self._tester.test_finished.connect(self._on_test_result)
        self._tester.start()

    def _on_test_result(self, success: bool, message: str) -> None:
        """测试完成回调（主线程）。"""
        # 清除看门狗
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.deleteLater()
            self._watchdog = None

        self._tester = None
        self._test_passed = success
        self._test_btn.setEnabled(True)
        self._test_btn.setText("🔌 重新测试")
        self._skip_label.setVisible(True)

        if success:
            self._result_label.setText(f"✅ {message}")
            self._result_label.setStyleSheet(
                "color: #2E7D32; font-weight: bold; font-size: 12pt;"
            )
        else:
            self._result_label.setText(f"❌ {message}")
            self._result_label.setStyleSheet(
                "color: #C0392B; font-weight: bold; font-size: 12pt;"
            )

        self.completeChanged.emit()

    def _on_watchdog_timeout(self) -> None:
        """看门狗超时：强制终止卡死的测试线程。"""
        self._abort_test()
        self._test_btn.setEnabled(True)
        self._test_btn.setText("🔌 重新测试")
        self._skip_label.setVisible(True)
        self._result_label.setVisible(True)
        self._result_label.setText("⚠️ 连接超时（超过 25 秒），请检查网络或跳过测试。")
        self._result_label.setStyleSheet("color: #E67E22; font-weight: bold; font-size: 12pt;")

    def _on_skip_test(self, _link: str = "") -> None:
        """跳过网络测试，直接标记为可完成。"""
        self._abort_test()
        self._test_passed = True
        self._test_btn.setEnabled(False)
        self._test_btn.setText("⏭ 已跳过")
        self._skip_label.setVisible(False)
        self._result_label.setVisible(True)
        self._result_label.setText("⏭ 已跳过连接测试，点击「完成」进入主程序。")
        self._result_label.setStyleSheet("color: #7F8C8D; font-weight: bold; font-size: 12pt;")
        self.completeChanged.emit()


# ===================================================================
# OnboardingWizard
# ===================================================================
class OnboardingWizard(QWizard):
    """
    初始化向导主窗口。

    用法：
        wizard = OnboardingWizard()
        wizard.show()

        # 向导完成后通过 signal_bus.onboarding_completed 信号通知应用切换。
    """

    def __init__(self, parent: QWizard | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnyBox · 初始化向导")
        self.setWindowIcon(QIcon(str(RESOURCE_DIR / "assets" / "anybox.ico")))
        self.setMinimumSize(600, 560)
        self.resize(640, 600)

        # 移除问号按钮，同时显式保留所有标准标题栏按钮（关闭、最小化、最大化）
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setWindowFlags(flags)

        # ---- 页面注册 ----
        self._page1 = _WelcomePage(self)
        self._page2 = _APISetupPage(self)
        self._page3 = _TestFinishPage(self)

        self.addPage(self._page1)
        self.addPage(self._page2)
        self.addPage(self._page3)

        # 样式
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setStyleSheet("""
            QWizard { background: #FAFAFA; }
            QWizard QLabel#titleLabel {
                font-size: 16px; font-weight: bold; color: #1976D2;
            }
            QWizard QPushButton {
                min-width: 90px; min-height: 30px;
            }
        """)

        # 铁律：初始状态下不允许直接完成（必须通过 Page 3 测试）
        self.button(QWizard.WizardButton.FinishButton).setEnabled(False)
        self._page3.completeChanged.connect(self._on_page3_completion_changed)

    # ------------------------------------------------------------------
    def _on_page3_completion_changed(self) -> None:
        """当 Page 3 的 isComplete() 状态变化时，同步 Finish 按钮。"""
        btn = self.button(QWizard.WizardButton.FinishButton)
        btn.setEnabled(self._page3.isComplete())

    # ------------------------------------------------------------------
    def done(self, result: int) -> None:
        """
        Wizard 完成时：
            1. 持久化配置到 anybox_config.json
            2. 通过 signal_bus 通知全局
        """
        if result == QWizard.DialogCode.Accepted:
            self._persist_config()

        super().done(result)

    def _persist_config(self) -> None:
        """将配置写入根目录 anybox_config.json。"""
        config = getattr(self, "_collected_config", None)
        if not isinstance(config, dict):
            logger.warning("向导完成时未找到有效配置数据。")
            return

        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"配置已保存至: {CONFIG_FILE}")
        except OSError as e:
            logger.error(f"配置写入失败: {e}")

        # 通知全局：向导已完成
        if signal_bus is not None:
            signal_bus.onboarding_completed.emit()