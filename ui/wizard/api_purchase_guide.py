"""
APIPurchaseGuide — "如何购买API" 引导弹窗

面向小白用户的 API 购买指引，涵盖：
  - DeepSeek  (deepseek.com)      — 国内注册 / 支付宝 / 微信支付
  - Kimi      (月之暗面)           — moonshot.cn
  - 通义千问   (阿里云百炼)         — dashscope.aliyuncs.com
  - 智谱 GLM  (智谱开放平台)        — open.bigmodel.cn
  - OpenAI    (国际用户)           — platform.openai.com

所有内容均为本地静态文本，无网络请求。网址自动转为可点击超链接。
"""

from __future__ import annotations

import html
import re

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QScrollArea,
    QTextBrowser,
    QWidget,
    QFrame,
    QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


# ---------------------------------------------------------------------------
# 各服务商购买指南（纯文本，URL 将被自动转为超链接）
# ---------------------------------------------------------------------------
GUIDES: dict[str, dict[str, str]] = {
    "deepseek": {
        "title": "DeepSeek API 购买指南",
        "overview": (
            "DeepSeek 是当前性价比最高的中文大模型之一，"
            "注册即送 10 元体验金，支持支付宝/微信充值。"
        ),
        "steps": (
            "\U0001f4cc 第1步：注册账号\n"
            "  打开 https://platform.deepseek.com ，使用手机号或邮箱注册。\n\n"
            "\U0001f4cc 第2步：获取 API Key\n"
            "  登录后，点击左侧菜单「API Keys」→「创建 API Key」，\n"
            "  复制生成的 sk- 开头的密钥并妥善保存。\n\n"
            "\U0001f4cc 第3步：充值（可选）\n"
            "  新用户自动获得 10 元免费额度。如需更多，点击「充值」\n"
            "  支持支付宝/微信支付，最低充值 10 元。\n\n"
            "\U0001f4cc 第4步：粘贴到 AnyBox\n"
            "  将 API Key 粘贴到本页面的「API Key」输入框，\n"
            "  选择 DeepSeek 服务商，点击下一步测试连接即可。\n\n"
            "\U0001f4b0 价格参考：deepseek-v4-flash 约 \u00a51/百万 tokens"
        ),
    },
    "kimi": {
        "title": "Kimi (月之暗面) API 购买指南",
        "overview": (
            "Kimi 由月之暗面科技推出，以超长上下文（128K）著称，"
            "适合处理长文档、合同分析等场景。"
        ),
        "steps": (
            "\U0001f4cc 第1步：注册账号\n"
            "  打开 https://platform.moonshot.cn ，使用手机号注册。\n\n"
            "\U0001f4cc 第2步：获取 API Key\n"
            "  登录后进入「控制台」→「API Keys」→「新建」，\n"
            "  复制生成的密钥。\n\n"
            "\U0001f4cc 第3步：充值\n"
            "  控制台 →「费用中心」→「充值」，支持支付宝/微信。\n"
            "  新用户通常有免费额度赠送。\n\n"
            "\U0001f4cc 第4步：粘贴到 AnyBox\n"
            "  将 API Key 粘贴到本页面，选择 Kimi 服务商即可。\n\n"
            "\U0001f4b0 价格参考：moonshot-v1-8k 约 \u00a51.2/百万 tokens"
        ),
    },
    "qwen": {
        "title": "通义千问 (阿里云百炼) API 购买指南",
        "overview": (
            "通义千问是阿里云旗下大模型，通过百炼平台提供 API 服务，"
            "新用户有大量免费额度。"
        ),
        "steps": (
            "\U0001f4cc 第1步：注册阿里云账号\n"
            "  若无阿里云账号，需首先注册：\n"
            "  https://account.aliyun.com/register/qr_register.htm"
            "?spm=a2c4g.11186623.0.0.2510172aYqmvb3\n\n"
            "\U0001f4cc 第2步：开通阿里云百炼\n"
            "  使用阿里云主账号前往阿里云百炼大模型服务平台：\n"
            "  https://bailian.console.aliyun.com/"
            "?spm=a2c4g.11186623.0.0.2510172aYqmvb3&tab=model#/model-market\n"
            "  阅读并同意协议后，将自动开通阿里云百炼。\n"
            "  如果未弹出服务协议，则表示您已经开通。\n\n"
            "\U0001f4cc 第3步：获取 API Key\n"
            "  前往 API Key 页面：\n"
            "  https://bailian.console.aliyun.com/"
            "?spm=a2c4g.11186623.0.0.2510172aYqmvb3#/api-key\n"
            "  单击【创建 API Key】，即可通过 API Key 调用大模型。\n"
            "  注意：API Key 仅创建时显示一次，请立即保存！\n\n"
            "\U0001f4cc 第4步：粘贴到 AnyBox\n"
            "  将 API Key 粘贴到本页面，选择「通义千问」服务商即可。\n\n"
            "\U0001f4b0 价格参考：qwen-turbo 约 \u00a50.8/百万 tokens，\n"
            "  新用户赠送百万 tokens 免费额度。"
        ),
    },
    "zhipu": {
        "title": "智谱 GLM API 购买指南",
        "overview": (
            "智谱 AI 由清华大学孵化，GLM 系列模型在中文理解上表现优异，"
            "注册即送额度。"
        ),
        "steps": (
            "\U0001f4cc 第1步：注册账号\n"
            "  打开 https://open.bigmodel.cn ，使用手机号注册。\n\n"
            "\U0001f4cc 第2步：获取 API Key\n"
            "  登录后进入「控制台」→「API Keys」→「创建新的 API Key」，\n"
            "  复制生成的密钥（含 . 分隔符的完整格式）。\n\n"
            "\U0001f4cc 第3步：充值（可选）\n"
            "  新用户注册赠送 18 元体验金。如需更多：\n"
            "  「费用中心」→「充值」，支持支付宝/微信。\n\n"
            "\U0001f4cc 第4步：粘贴到 AnyBox\n"
            "  将 API Key 粘贴到本页面，选择「智谱 GLM」服务商即可。\n\n"
            "\U0001f4b0 价格参考：glm-4-flash 约 \u00a50.1/百万 tokens"
        ),
    },
    "openai": {
        "title": "OpenAI API 购买指南",
        "overview": (
            "OpenAI 是全球领先的 AI 公司，提供 GPT 系列模型。\n"
            "⚠\ufe0f 需要国际信用卡，且需科学上网。国内用户推荐使用 DeepSeek 等替代。"
        ),
        "steps": (
            "\U0001f4cc 第1步：注册账号\n"
            "  打开 https://platform.openai.com ，使用邮箱注册（需海外手机号验证）。\n\n"
            "\U0001f4cc 第2步：获取 API Key\n"
            "  登录后进入「API Keys」→「Create new secret key」，\n"
            "  复制 sk- 开头的密钥并妥善保存（仅显示一次）。\n\n"
            "\U0001f4cc 第3步：充值\n"
            "  「Billing」→「Add payment method」→ 绑定国际信用卡。\n"
            "  支持 Visa / MasterCard，最低充值 $5。\n\n"
            "\U0001f4cc 第4步：粘贴到 AnyBox\n"
            "  将 API Key 粘贴到本页面，选择 OpenAI 或 Custom 服务商。\n\n"
            "\U0001f4b0 价格参考：gpt-4o 约 $2.5/百万 input tokens"
        ),
    },
    "custom": {
        "title": "自定义 / 自部署 API 指南",
        "overview": (
            "除了商业 API，你还可以使用免费或自部署的大模型服务。"
        ),
        "steps": (
            "\U0001f527 方案一：Ollama（完全免费/本地运行）\n"
            "  1. 下载 Ollama：https://ollama.com\n"
            "  2. 运行：ollama run qwen2.5:7b\n"
            "  3. Base URL 填写：http://localhost:11434/v1\n"
            "  4. API Key 任意非空字符串即可\n\n"
            "\U0001f527 方案二：硅基流动 (SiliconFlow)\n"
            "  1. 注册：https://siliconflow.cn\n"
            "  2. 新用户赠送额度，支持支付宝充值\n"
            "  3. Base URL：https://api.siliconflow.cn/v1\n\n"
            "\U0001f527 方案三：其他 OpenAI 兼容接口\n"
            "  任何兼容 /v1/chat/completions 的服务均可使用，\n"
            "  在 Custom 模式下填入对应 Base URL 和 API Key 即可。"
        ),
    },
}

# 向导页（注册时）显示的服务商列表
WIZARD_PROVIDERS = ["deepseek", "kimi", "qwen", "zhipu", "custom"]

# Settings 弹窗显示的服务商列表
SETTINGS_PROVIDERS = ["deepseek", "kimi", "qwen", "zhipu", "openai", "custom"]


# ---------------------------------------------------------------------------
# 辅助函数：将纯文本中的 URL 转为 <a> 标签
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"(https?://[^\s\u3002\uff0c\uff0e\n\uff09\uff09]+)")


def _plain_text_to_rich_html(text: str) -> str:
    """将纯文本中的 http/https 链接转为可点击的 HTML 超链接。"""
    escaped = html.escape(text)
    rich = _URL_RE.sub(r'<a href="\1">\1</a>', escaped)
    body = rich.replace("\n\n", "<br><br>").replace("\n", "<br>")
    return (
        '<html><body style="font-family: Microsoft YaHei; font-size: 10pt; '
        'line-height: 1.7; color: #333;">'
        + body
        + "</body></html>"
    )


# ===================================================================
# APIPurchaseGuideDialog
# ===================================================================
class APIPurchaseGuideDialog(QDialog):
    """「如何购买 API」模态引导弹窗。

    使用 QTabWidget 展示各服务商的购买步骤，
    内容纯静态文本，无需网络请求。
    """

    def __init__(
        self,
        provider_keys: list[str] | None = None,
        parent=None,
    ) -> None:
        """
        Args:
            provider_keys: 需要显示的服务商 key 列表。
                           若为 None，则显示全部。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setWindowTitle("🛒 如何购买 API — 小白指引")
        self.setMinimumSize(640, 520)
        self.resize(680, 560)

        if provider_keys is None:
            provider_keys = list(GUIDES.keys())

        self._build_ui(provider_keys)

    # ------------------------------------------------------------------
    def _build_ui(self, provider_keys: list[str]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # 标题
        title = QLabel("🛒 如何获取大模型 API Key")
        title.setFont(QFont("Microsoft YaHei", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #1976D2;")
        layout.addWidget(title)

        subtitle = QLabel(
            "不同服务商的注册和购买流程略有差异，请根据你选择的服务商查看对应教程。\n"
            "所有服务商均支持支付宝/微信支付（OpenAI 除外），注册即送免费体验额度。"
        )
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Microsoft YaHei", 9))
        subtitle.setStyleSheet("color: #666; margin-bottom: 6px;")
        layout.addWidget(subtitle)

        # ---- 分隔线 ----
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #E0E0E0;")
        layout.addWidget(line)

        # ---- 选项卡 ----
        tabs = QTabWidget()
        tabs.setFont(QFont("Microsoft YaHei", 10))
        tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #DDD; border-radius: 6px; "
            "background: #FAFAFA; }"
            "QTabBar::tab { padding: 8px 18px; margin-right: 2px; "
            "border: 1px solid #DDD; border-bottom: none; "
            "border-top-left-radius: 6px; border-top-right-radius: 6px; "
            "background: #F0F0F0; }"
            "QTabBar::tab:selected { background: white; font-weight: bold; "
            "border-bottom: 2px solid #1976D2; }"
            "QTabBar::tab:hover { background: #E3F2FD; }"
        )

        for key in provider_keys:
            guide = GUIDES.get(key)
            if guide is None:
                continue
            tab = self._create_guide_tab(guide)
            tabs.addTab(tab, guide["title"].replace(" API 购买指南", ""))

        layout.addWidget(tabs, 1)

        # ---- 底部关闭按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("我知道了")
        close_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        close_btn.setFixedSize(140, 38)
        close_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 6px; border: none; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:pressed { background: #0D47A1; }"
        )
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _create_guide_tab(self, guide: dict[str, str]) -> QWidget:
        """为单个服务商创建指引标签页。"""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(12)

        # 概述
        overview = QLabel(guide["overview"])
        overview.setWordWrap(True)
        overview.setFont(QFont("Microsoft YaHei", 10))
        overview.setStyleSheet(
            "color: #333; background: #E3F2FD; padding: 10px 14px; "
            "border-radius: 8px; line-height: 1.5;"
        )
        page_layout.addWidget(overview)

        # 步骤（可滚动，使用 QTextBrowser 支持可点击超链接）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        steps_browser = QTextBrowser()
        steps_browser.setOpenExternalLinks(True)  # 点击链接 → 系统浏览器打开
        steps_browser.setFont(QFont("Microsoft YaHei", 10))
        steps_browser.setStyleSheet(
            "QTextBrowser { color: #333; background: transparent; border: none; "
            "padding: 4px; }"
            "QTextBrowser a  { color: #1976D2; text-decoration: none; "
            "font-weight: bold; }"
        )
        steps_browser.setReadOnly(True)
        steps_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        html = _plain_text_to_rich_html(guide["steps"])
        steps_browser.setHtml(html)

        scroll.setWidget(steps_browser)
        page_layout.addWidget(scroll, 1)

        return page