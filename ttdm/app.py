"""应用入口：日志、主题、主窗口。"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import Store
from .paths import APP_NAME, APP_TITLE, APP_VERSION, data_dir
from . import i18n
from .ui import icons, theme
from .ui.window import MainWindow

log = logging.getLogger(__name__)


def setup_logging() -> Path:
    """把运行日志写到数据目录，方便排查网络与解码问题。写不进去就只往 stderr 输出。"""
    log_dir = data_dir() / "logs"
    path = log_dir / "app.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return path
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    return path


def app_icon() -> QIcon:
    """用主题色画一个圆角图标，避免额外资源文件。"""
    icon = QIcon()
    for size in (32, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        radius = size * 0.22
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), radius, radius)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QColor(theme.ACCENT))
        gradient.setColorAt(1.0, QColor(theme.ACCENT_DARK))
        painter.fillPath(path, gradient)
        inset = size * 0.24
        icons.paint_icon(
            painter,
            "film",
            QRectF(inset, inset, size - inset * 2, size - inset * 2),
            QColor("#FFFFFF"),
            max(1.4, size * 0.026),
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _set_process_id() -> None:
    """让任务栏把窗口归到独立条目，而不是 python.exe。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "%s.%s" % (APP_NAME, APP_VERSION)
        )
    except Exception:  # noqa: BLE001 - 非 Windows 或旧系统忽略
        pass


def _install_excepthook(window: Optional[MainWindow]) -> None:
    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("未捕获异常\n%s", text)
        if window is not None:
            try:
                window.ctx.notify("出现异常，详情见日志", "alert", theme.ACCENT)
            except Exception:  # noqa: BLE001
                pass
            QMessageBox.warning(window, "出现异常", "%s\n\n日志：%s" % (exc, data_dir() / "logs" / "app.log"))

    sys.excepthook = hook


def main(argv: Optional[list[str]] = None) -> int:
    log_path = setup_logging()
    _set_process_id()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    store = Store()
    # 语言要在建窗口之前定好，控件显示时就能套上译文
    i18n.set_language(store.settings.language, notify=False)
    i18n.install(app)
    # 先定深浅色，再应用样式，避免启动瞬间闪一下另一种配色
    theme.set_mode(store.settings.theme)
    # 自定义强调色要在生成 QSS 之前套上
    theme.set_accent("dark", store.settings.accent_dark)
    theme.set_accent("light", store.settings.accent_light)
    theme.apply_theme(app)
    app.setWindowIcon(app_icon())

    log.info("启动 %s %s，日志 %s", APP_TITLE, APP_VERSION, log_path)
    window = MainWindow(store)
    _install_excepthook(window)
    window.show()
    code = app.exec()
    # 关闭窗口即结束进程：不让卡住的下载线程把进程留在后台
    logging.shutdown()
    os._exit(int(code or 0))


if __name__ == "__main__":
    sys.exit(main())
