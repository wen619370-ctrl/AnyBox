# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AnyBox (方案一：单文件 + UPX 压缩)
"""
import os
from pathlib import Path

# SPECPATH 是 PyInstaller exec spec 时注入的变量，指向 spec 文件所在目录
ROOT = Path(SPECPATH)

# --- 隐藏导入（PySide6 常缺少隐式依赖） ---
hiddenimports = [
    # PySide6 核心
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    # 自定义模块
    "core",
    "core.executor",
    "core.llm_controller",
    "core.dependency_manager",
    "ui",
    "ui.main_window",
    "ui.main_window.chat_view",
    "ui.main_window.tool_card",
    "ui.main_window.tool_library_view",
    "ui.main_window.settings_dialog",
    "ui.tool_executor",
    "ui.tool_executor.dialog",
    "ui.tool_executor.ui_builder",
    "ui.wizard",
    "ui.wizard.onboarding_wizard",
    "ui.wizard.api_purchase_guide",
    "utils",
    "utils.logger",
    "data",
    # 第三方
    "PIL",
    "PIL.Image",
    "requests",
]

# --- 排除模块（减小体积） ---
excluded = [
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "sqlalchemy",
    "PyQt5",
    "wx",
    "curses",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "email",
    "http",
    "xmlrpc",
    "pydoc",
    "doctest",
    "unittest",
    "test",
    "cv2",
    "cffi",
    "tensorflow",
    "torch",
    "OpenGL",
    "pkg_resources",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),  # 图标等静态资源
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded,
    noarchive=False,
    optimize=0,
)

# --- UPX 压缩配置 ---
upx_path = str(ROOT / "upx.exe")
if not os.path.exists(upx_path):
    upx_path = None  # 回退：不使用 UPX

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    [],
    a.datas,
    name="AnyBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,              # 启用 UPX
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口（GUI 应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "anybox.ico"),
)
