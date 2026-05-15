"""
ui — 用户界面层

本包承载 AnyBox 全部前端界面，按功能划分为三个子包：
    - wizard       : 初始化向导 (OnboardingWizard)
    - main_window  : 主界面双轨制视图 (ChatCreateView / ToolLibraryView)
    - tool_executor: 动态工具执行器弹窗 (DynamicToolDialog / DynamicUIBuilder)
"""

# ---------------------------------------------------------------------------
# 顶层公开导出（便捷导入路径）
# ---------------------------------------------------------------------------
from ui.wizard.onboarding_wizard import OnboardingWizard
from ui.main_window.chat_view import ChatCreateView
from ui.main_window.tool_library_view import ToolLibraryView
from ui.tool_executor.dialog import DynamicToolDialog

__all__ = [
    "OnboardingWizard",
    "ChatCreateView",
    "ToolLibraryView",
    "DynamicToolDialog",
]