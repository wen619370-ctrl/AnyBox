"""
AnyBox 统一日志系统

功能：
    1. 文件日志 (logs/anybox.log) — 自动轮转，保留最近 5 个文件，单文件 ≤ 5MB
    2. 控制台日志 (stderr) — 开发调试用
    3. QLogSignal / QLogHandler — 可选 GUI 日志信号，供日志面板实时展示

用法：
    from utils.logger import setup_logging, get_logger

    setup_logging()          # 在 main() 最开头调用一次
    logger = get_logger(__name__)
    logger.info("应用启动")
"""

import logging
import logging.handlers
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

# ---------------------------------------------------------------------------
# 路径常量 — 需兼容 PyInstaller frozen 环境
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
LOG_DIR = ROOT_DIR / "logs"
LOG_FILE = LOG_DIR / "anybox.log"

# 文件轮转策略
MAX_BYTES = 5 * 1024 * 1024   # 5MB
BACKUP_COUNT = 5

# ---------------------------------------------------------------------------
# 日志格式
# ---------------------------------------------------------------------------
FILE_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

CONSOLE_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# GUI 日志信号 — 供日志面板实时展示
# ---------------------------------------------------------------------------
class QLogSignal(QObject):
    """
    单例信号发射器。日志面板通过连接此信号实时展示日志。
    signal 参数: (timestamp_float, level_str, logger_name, message)
    """
    message_logged = Signal(float, str, str, str)


_log_signal: Optional[QLogSignal] = None


def get_log_signal() -> QLogSignal:
    """获取全局 QLogSignal 单例。"""
    global _log_signal
    if _log_signal is None:
        _log_signal = QLogSignal()
    return _log_signal


class QLogHandler(logging.Handler):
    """
    自定义 logging.Handler，将日志记录转发到 QLogSignal.message_logged。

    安装此 handler 后，所有日志同时输出到：
        - 文件 (logs/anybox.log)
        - 控制台 (stderr)
        - GUI 日志面板 (通过 QLogSignal)
    """

    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._signal = get_log_signal()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._signal.message_logged.emit(
                record.created,        # float 时间戳
                record.levelname,      # "INFO" / "DEBUG" / ...
                record.name,           # logger 名称
                msg,
            )
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# 安装函数
# ---------------------------------------------------------------------------
_initialized = False


def setup_logging(
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    enable_gui_handler: bool = True,
) -> None:
    """
    初始化 AnyBox 全项目日志系统。应在 main() 最开头调用一次。

    Args:
        console_level:   控制台 stderr 输出的最低级别 (默认 INFO)
        file_level:      文件输出的最低级别 (默认 DEBUG)
        enable_gui_handler: 是否安装 GUI 信号 handler (默认 True)

     副作用：
        - 设置 root logger 级别为 DEBUG
        - 添加 RotatingFileHandler → logs/anybox.log
        - 添加 StreamHandler → stderr
        - (可选) 添加 QLogHandler → QLogSignal
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    # ---- 确保日志目录存在 ----
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 获取 root logger ----
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root 接受所有级别，由各 handler 自行过滤

    # 避免重复添加（防御式编程）
    if root.handlers:
        # 如果已有 handler，不再重复安装
        return

    # ---- 1. 文件 Handler：轮转日志 ----
    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(FILE_FORMAT)
    root.addHandler(file_handler)

    # ---- 2. 控制台 Handler ----
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(CONSOLE_FORMAT)
    root.addHandler(console_handler)

    # ---- 3. GUI 信号 Handler (可选) ----
    if enable_gui_handler:
        gui_handler = QLogHandler()
        gui_handler.setLevel(logging.INFO)  # GUI 面板默认只显示 INFO+
        gui_handler.setFormatter(
            logging.Formatter(
                fmt="%(levelname)-7s | %(name)-22s | %(message)s"
            )
        )
        root.addHandler(gui_handler)

    # ---- 抑制 Qt 内部 DEBUG 噪音 ----
    logging.getLogger("PySide6").setLevel(logging.WARNING)

    # ---- 首条日志 ----
    root.info("=" * 60)
    root.info(f"AnyBox 日志系统已初始化 | 日志文件: {LOG_FILE}")
    root.info("=" * 60)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 logger。等价于 logging.getLogger(name)，
    但确保日志系统已初始化（若未初始化则轻量降级为 console-only）。

    Args:
        name: 通常使用 __name__
    """
    if not _initialized:
        # 降级启动：仅 console，避免静默丢失日志
        _fallback_console()
    return logging.getLogger(name)


def _fallback_console() -> None:
    """未调用 setup_logging 时的最低保底：仅 console handler。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(CONSOLE_FORMAT)
        root.addHandler(handler)
    global _initialized
    _initialized = True