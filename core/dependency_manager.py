"""
DependencyManager — 沙盒依赖管理器

核心职责：
    1. 正则扫描脚本中的 # pip: 包名 注释，解析依赖清单
    2. 使用沙盒 Python 的 pip 静默安装缺失第三方库
    3. 解析 stderr 中的 ModuleNotFoundError / ImportError 兜底安装

所有小工具共享同一个沙盒环境，不使用 venv。
"""

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
def _get_app_root() -> Path:
    """返回应用程序根目录。

    开发环境：当前文件所在目录的上级目录 (项目根)。
    PyInstaller 打包后：exe 所在目录（而非临时解压目录 _MEIPASS）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _get_app_root()
DEFAULT_SANDBOX_DIR = ROOT_DIR / "sandbox" / "python-embed"

# 匹配 # pip: 包名  或  # pip: 包名==版本号
_RE_PIP_COMMENT = re.compile(r"^\s*#\s*pip:\s*([a-zA-Z0-9_\-\.\[\]=><~!;,\s]+)\s*$")

# 匹配 ModuleNotFoundError: No module named 'xxx'
_RE_MODULE_NOT_FOUND = re.compile(
    r"ModuleNotFoundError:\s*(?:No module named\s*['\"]([^'\"]+)['\"]?)",
    re.IGNORECASE,
)
# 匹配 ImportError: No module named 'xxx'
_RE_IMPORT_ERROR = re.compile(
    r"ImportError:\s*(?:No module named\s*['\"]([^'\"]+)['\"]?)",
    re.IGNORECASE,
)

logger = logging.getLogger("anybox.deps")


# ---------------------------------------------------------------------------
# DependencyManager
# ---------------------------------------------------------------------------
class DependencyManager:
    """
    沙盒依赖管理器。

    用法：
        dm = DependencyManager(sandbox_dir="path/to/python-embed")
        dm.install_from_comments(script_path)  # 步骤1：扫描注释并安装
        dm.resolve_from_stderr(stderr_text)    # 步骤2：从报错中解析并安装
    """

    PIP_INSTALL_TIMEOUT = 120  # 单包安装超时秒数

    def __init__(self, sandbox_dir: str | None = None) -> None:
        base = Path(sandbox_dir) if sandbox_dir else DEFAULT_SANDBOX_DIR
        self._sandbox_dir = base

        # 定位沙盒 python 解释器
        self._python_exe = self._locate_python(base)
        if self._python_exe:
            logger.info(f"沙盒 Python: {self._python_exe}")
        else:
            logger.warning(f"沙盒 Python 未找到（路径: {base}），依赖安装将不可用。")

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def install_from_comments(self, script_path: str) -> list[str]:
        """
        步骤1：扫描脚本中的 `# pip: 包名` 注释，安装所有声明的依赖。

        Args:
            script_path: .py 脚本路径

        Returns:
            实际安装成功的包名列表
        """
        packages = self._scan_pip_comments(script_path)
        if not packages:
            return []

        logger.info(f"从注释中解析到依赖: {packages}")
        installed = []
        for pkg in packages:
            ok = self.install_package(pkg)
            if ok:
                installed.append(pkg)
        return installed

    def resolve_from_stderr(self, stderr_text: str) -> list[str]:
        """
        步骤2：从 stderr 的 Traceback 中解析 ModuleNotFoundError/ImportError，
        尝试安装缺失的包。

        Args:
            stderr_text: 脚本的 stderr 输出

        Returns:
            成功安装的包名列表
        """
        if not stderr_text:
            return []

        modules: set[str] = set()

        for match in _RE_MODULE_NOT_FOUND.finditer(stderr_text):
            modules.add(match.group(1))

        for match in _RE_IMPORT_ERROR.finditer(stderr_text):
            modules.add(match.group(1))

        if not modules:
            return []

        logger.info(f"从 stderr 解析到缺失模块: {modules}")
        installed = []
        for mod in modules:
            pkg = self._resolve_package_name(mod)
            if self.install_package(pkg):
                installed.append(pkg)
        return installed

    def install_package(self, package_spec: str) -> bool:
        """
        使用沙盒 pip 安装单个包。

        Args:
            package_spec: 包名或版本约束，如 "Pillow" 或 "openpyxl>=3.0"

        Returns:
            True 表示安装成功
        """
        if not self._python_exe:
            logger.error("沙盒 Python 不可用，无法安装依赖。")
            return False

        # 构造 pip install 命令
        # 为最大化兼容性，调用沙盒 python -m pip install
        cmd = [
            self._python_exe,
            "-m", "pip", "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            package_spec,
        ]

        logger.info(f"安装依赖: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.PIP_INSTALL_TIMEOUT,
                cwd=str(self._sandbox_dir),
                # 确保使用沙盒的环境变量
                env={
                    "PYTHONIOENCODING": "utf-8",
                },
            )
            if result.returncode == 0:
                logger.info(f"依赖安装成功: {package_spec}")
                return True
            else:
                stderr_tail = result.stderr.strip()[-300:] if result.stderr else "(无 stderr)"
                logger.warning(f"依赖安装失败 ({package_spec}): {stderr_tail}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"依赖安装超时: {package_spec}")
            return False
        except Exception as e:
            logger.error(f"依赖安装异常 ({package_spec}): {e}")
            return False

    @property
    def is_available(self) -> bool:
        """沙盒 Python 是否可用。"""
        return self._python_exe is not None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    @staticmethod
    def _locate_python(base: Path) -> str | None:
        """在沙盒目录中定位 python 解释器。"""
        candidates = [
            base / "python.exe",
            base / "python3.exe",
            base / "bin" / "python3",
            base / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    @staticmethod
    def _scan_pip_comments(script_path: str) -> list[str]:
        """
        扫描脚本文件中的 # pip: 注释行，解析包名列表。

        支持格式：
            # pip: Pillow
            # pip: openpyxl>=3.0
            # pip: requests, beautifulsoup4
        """
        packages: list[str] = []
        try:
            script = Path(script_path)
            if not script.is_file():
                return packages

            for line in script.read_text(encoding="utf-8").splitlines():
                m = _RE_PIP_COMMENT.match(line)
                if m:
                    # 一行可能声明多个包（逗号分隔）
                    raw = m.group(1)
                    for pkg in raw.split(","):
                        pkg = pkg.strip()
                        if pkg:
                            packages.append(pkg)
        except OSError as e:
            logger.error(f"读取脚本失败: {e}")
        return packages

    @staticmethod
    def _resolve_package_name(module_name: str) -> str:
        """
        将模块名映射为 pip 包名。

        常见映射表（Python 模块名 ≠ pip 包名的情况）。
        """
        MAPPING: dict[str, str] = {
            "PIL": "Pillow",
            "cv2": "opencv-python",
            "bs4": "beautifulsoup4",
            "yaml": "pyyaml",
            "sklearn": "scikit-learn",
            "dotenv": "python-dotenv",
            "dateutil": "python-dateutil",
        }
        return MAPPING.get(module_name, module_name)