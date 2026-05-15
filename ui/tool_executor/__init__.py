"""
ui.tool_executor — 动态工具执行器子包

提供根据 JSON UI 协议动态渲染原生 PySide6 控件，并通过 QProcess
在沙盒 Python 环境中异步执行脚本的完整交互弹窗。
    - DynamicToolDialog : 非模态工具执行弹窗
    - DynamicUIBuilder  : JSON UI → PySide6 控件动态渲染引擎
"""

from ui.tool_executor.dialog import DynamicToolDialog
from ui.tool_executor.ui_builder import DynamicUIBuilder

__all__ = [
    "DynamicToolDialog",
    "DynamicUIBuilder",
]