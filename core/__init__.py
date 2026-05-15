"""
core — 核心基础设施层

本包提供 AnyBox 所有与 UI 无关的核心能力，包括：
    - ScriptExecutor   : QProcess 沙盒脚本异步执行器
    - LLMController    : 大模型 API 客户端 + System Prompt 封装
    - DependencyManager: 沙盒 pip 依赖管理器

典型用法:
    from core import ScriptExecutor, LLMController, DependencyManager
"""

# ---------------------------------------------------------------------------
# 公开导出
# ---------------------------------------------------------------------------
from core.executor import ScriptExecutor
from core.llm_controller import LLMController, save_tool, SYSTEM_PROMPT
from core.dependency_manager import DependencyManager

__all__ = [
    "ScriptExecutor",
    "LLMController",
    "DependencyManager",
    "save_tool",
    "SYSTEM_PROMPT",
]