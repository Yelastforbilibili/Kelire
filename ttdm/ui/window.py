"""主窗口：侧栏 + 顶栏 + 页面栈 + 线路测速调度 + 下载事件桥。"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Store
from ..core.downloader import DownloadManager, DownloadProgress
from ..core.http import HttpClient
from ..core.mirrors import FALLBACK_MIRRORS, MirrorReport, refresh_mirrors as measure_mirrors
from ..core.proxy import HlsProxy
from ..core import masking
from ..core.site import CATEGORIES, PlaySource, SiteApi
from .. import i18n
from ..i18n import tr
from ..paths import APP_TITLE, APP_VERSION, hls_cache_dir, image_cache_dir
from . import components, icons, theme
from .components import IconButton, Toast
from .context import AppContext
from .imagecache import ImageCache
from .onboarding import OnboardingOverlay
from .pages.browse import BrowsePage
from .pages.detail import DetailPage
from .pages.downloads import DownloadsPage
from .pages.home import HomePage
from .pages.player import PlayerPage
from .pages.search import SearchPage
from .pages.settings import SettingsPage
from .workers import TaskRunner

log = logging.getLogger(__name__)

NAV_ITEMS = (
    ("home", "首页", "home"),
    *[("type:%d" % type_id, name, icon_name) for (name, type_id), icon_name in zip(
        CATEGORIES, ("star", "flame", "globe", "moon", "rows", "film")
    )],
    ("downloads", "下载管理", "download"),
    ("settings", "设置", "setting"),
)


def _alive(widget) -> bool:
    try:
        return bool(widget) and widget.isVisible()
    except RuntimeError:
        return False


class _DownloadBridge(QObject):
    """把工作线程里的下载事件转成 Qt 信号。"""

    progress = Signal(object)

    def __call__(self, progress: DownloadProgress) -> None:
        self.progress.emit(progress)


class Sidebar(QFrame):
    navigate = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(222)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedSize(34, 34)
        self.mark.setPixmap(self._mark_pixmap())
        brand.addWidget(self.mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_label = QLabel(APP_TITLE)
        self.brand_label.setObjectName("Brand")
        brand_text.addWidget(self.brand_label)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(18)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self.category_buttons: dict[int, QPushButton] = {}
        self._sections: list[QLabel] = []
        self._categories_label: Optional[QLabel] = None
        for key, text, icon_name in NAV_ITEMS:
            if key == "downloads":
                layout.addSpacing(8)
                label = self._section_label("我的")
                self._sections.append(label)
                layout.addWidget(label)
            elif key == "settings":
                pass
            elif key == "type:6":
                layout.addSpacing(6)
                self._categories_label = self._section_label("追番分类")
                self._sections.append(self._categories_label)
                layout.addWidget(self._categories_label)
            button = QPushButton(text)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icons.icon(icon_name, 17, theme.TEXT_DIM))
            button.setIconSize(QSize(17, 17))
            button._icon_name = icon_name
            button._icon_color = theme.TEXT_DIM
            button.setMinimumHeight(38)
            button.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button
            if key.startswith("type:"):
                self.category_buttons[int(key.split(":", 1)[1])] = button
        layout.addStretch(1)

        self.badge = QLabel(self.buttons["downloads"])
        self.badge.setStyleSheet(
            "QLabel { background: %s; color: %s; border-radius: 9px;"
            " font-size: 10.5px; font-weight: 700; }" % (theme.ACCENT, theme.ON_ACCENT)
        )
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge.hide()

        self.status = QFrame()
        self.status.setObjectName("Panel")
        status_layout = QVBoxLayout(self.status)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(4)
        line = QHBoxLayout()
        line.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(7, 7)
        self._paint_dot(theme.GREEN)
        line.addWidget(self.dot)
        self.mirror_name = QLabel("正在测速…")
        self.mirror_name.setObjectName("Muted")
        line.addWidget(self.mirror_name)
        line.addStretch(1)
        status_layout.addLayout(line)
        self.mirror_hint = QLabel("启动时自动挑选最快线路")
        self.mirror_hint.setObjectName("Micro")
        self.mirror_hint.setWordWrap(True)
        status_layout.addWidget(self.mirror_hint)
        layout.addWidget(self.status)

        self.version_label = QLabel("v%s" % APP_VERSION)
        self.version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.version_label)
        self.refresh_theme()

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "QLabel { font-size: 10px; color: %s; letter-spacing: 1.2px; padding-left: 4px; }"
            % theme.TEXT_FAINT
        )
        return label

    def _mark_pixmap(self) -> QPixmap:
        size = 34
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 10, 10)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QColor(theme.ACCENT))
        gradient.setColorAt(1.0, QColor(theme.ACCENT_DARK))
        painter.fillPath(path, gradient)
        painter.setPen(Qt.NoPen)
        inset = 8.0
        icons.paint_icon(
            painter,
            "film",
            QRectF(inset, inset, size - inset * 2, size - inset * 2),
            QColor("#FFFFFF"),
            1.7,
        )
        painter.end()
        return pixmap

    def _paint_dot(self, color: str) -> None:
        pixmap = QPixmap(7, 7)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(0, 0, 7, 7)
        painter.end()
        self.dot.setPixmap(pixmap)

    def set_categories(self, type_ids) -> None:
        """按设置只显示勾选的分类，分区标题跟着一起隐藏。"""
        allowed = {int(item) for item in type_ids}
        for type_id, button in self.category_buttons.items():
            button.setVisible(type_id in allowed)
        if self._categories_label is not None:
            self._categories_label.setVisible(bool(allowed))

    def refresh_theme(self) -> None:
        self.mark.setPixmap(self._mark_pixmap())
        self.brand_label.setStyleSheet("")
        for label in self._sections:
            label.setStyleSheet(
                "QLabel { font-size: 10px; color: %s; letter-spacing: 1.2px; padding-left: 4px; }"
                % theme.TEXT_FAINT
            )
        self.version_label.setObjectName("Micro")
        self.mirror_name.setObjectName("Muted")
        self.mirror_hint.setObjectName("Micro")
        self.badge.setStyleSheet(
            "QLabel { background: %s; color: %s; border-radius: 9px;"
            " font-size: 10.5px; font-weight: 700; }" % (theme.ACCENT, theme.ON_ACCENT)
        )
        for button in self.buttons.values():
            name = getattr(button, "_icon_name", "")
            color = theme.ACCENT if button.isChecked() else theme.TEXT_DIM
            if name:
                button.setIcon(icons.icon(name, 17, color))
                button._icon_color = color
            button.setIconSize(QSize(17, 17))

    def set_active(self, key: str) -> None:
        self.group.setExclusive(False)
        for name, button in self.buttons.items():
            checked = name == key
            button.setChecked(checked)
            components.recolor_button_icon(
                button, theme.ACCENT if checked else theme.TEXT_DIM
            )
        self.group.setExclusive(True)

    def set_mirror(self, base: str, latency: Optional[float], alive: int, total: int) -> None:
        if not base:
            self.mirror_name.setText("没有可用线路")
            self.mirror_hint.setText("稍后自动重试，也可以在设置里手动测速")
            self._paint_dot(theme.ACCENT)
            return
        host = base.replace("https://", "").replace("http://", "").rstrip("/")
        self.mirror_name.setText(host)
        self.dot.setToolTip("点击顶栏线路标签可重新测速")
        if not latency or not total:
            self._paint_dot(theme.GOLD)
            self.mirror_hint.setText("手动切换的线路，稍后自动测速核对")
            return
        self._paint_dot(theme.GREEN if alive else theme.ACCENT)
        self.mirror_hint.setText(
            tr("延迟 {ms} ms · 存活 {alive}/{total}", ms=int(latency), alive=alive, total=total)
        )

    def set_measuring(self) -> None:
        self._paint_dot(theme.GOLD)
        self.mirror_hint.setText("正在测速并挑选最快线路…")

    def set_download_count(self, count: int) -> None:
        if count <= 0:
            self.badge.hide()
            return
        self.badge.setText(str(count))
        self.badge.adjustSize()
        width = max(20, self.badge.width() + 6)
        button = self.buttons["downloads"]
        self.badge.setGeometry(
            max(0, button.width() - width - 12),
            (button.height() - 18) // 2,
            width,
            18,
        )
        self.badge.show()
        self.badge.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self.badge.isVisible():
            self.set_download_count(int(self.badge.text() or 0))


class TopBar(QFrame):
    search_submitted = Signal(str)
    back_requested = Signal()
    mirror_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(66)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 22, 12)
        layout.setSpacing(12)

        self.back_button = IconButton("chevron-left", 36, 20, theme.TEXT_DIM, theme.TEXT)
        self.back_button.setToolTip("返回（Alt+←）")
        self.back_button.clicked.connect(self.back_requested)
        layout.addWidget(self.back_button)

        self.search = QLineEdit()
        self.search.setObjectName("SearchField")
        self.search.setPlaceholderText("搜索动漫名称、角色或年份")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(280)
        self.search.returnPressed.connect(self._emit_search)
        search_action = self.search.addAction(
            icons.icon("search", 16, theme.TEXT_FAINT), QLineEdit.LeadingPosition
        )
        search_action.setToolTip("搜索")
        self._search_action = search_action
        layout.addWidget(self.search, 1)

        self.mirror_button = QPushButton("正在选择线路")
        self.mirror_button.setObjectName("Chip")
        self.mirror_button.setCursor(Qt.PointingHandCursor)
        self.mirror_button.setIcon(icons.icon("speed", 14, theme.TEXT_DIM))
        self.mirror_button.setIconSize(QSize(14, 14))
        self.mirror_button.setToolTip("点击重新测速并切换最快线路")
        self.mirror_button.clicked.connect(self.mirror_clicked)
        layout.addWidget(self.mirror_button)

    def _emit_search(self) -> None:
        text = self.search.text().strip()
        if text:
            self.search_submitted.emit(text)

    def refresh_theme(self) -> None:
        self.mirror_button.setIcon(icons.icon("speed", 14, theme.TEXT_DIM))
        if getattr(self, "_search_action", None) is not None:
            self._search_action.setIcon(icons.icon("search", 16, theme.TEXT_FAINT))

    def set_mirror_text(self, text: str) -> None:
        self.mirror_button.setText(text)

    def set_search_text(self, text: str) -> None:
        self.search.setText(text)


class MainWindow(QWidget):
    mirror_switched = Signal(str)

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.settings = store.settings
        self.toast = None
        self._pages: dict[str, QWidget] = {}
        self._entries: list[dict] = []
        self._mirror_busy = False
        self._cinema = False
        self._immersive = False
        self._normal_geometry = None
        self._changing_page = False
        self._onboarding: Optional[OnboardingOverlay] = None

        self.client = HttpClient()
        self.api = SiteApi(self.client)
        self.api.set_base(
            self.settings.mirror_override or store.last_mirror or FALLBACK_MIRRORS[1]
        )
        self.api.on_failover = self._on_api_failover
        self.proxy = HlsProxy(hls_cache_dir(), self.settings.cache_limit_mb)
        self.images = ImageCache(image_cache_dir())
        self.tasks = TaskRunner(self, 6)
        self.bridge = _DownloadBridge(self)
        self.downloads = DownloadManager(
            self.api, lambda: self.ctx.settings, self.bridge
        )
        self.ctx = AppContext(
            store=store,
            settings=self.settings,
            api=self.api,
            client=self.client,
            proxy=self.proxy,
            images=self.images,
            tasks=self.tasks,
            downloads=self.downloads,
        )

        self._build_ui()
        self._build_shortcuts()

        self.bridge.progress.connect(self._on_download_progress)
        self.mirror_switched.connect(self._on_mirror_switched)

        self._mirror_timer = QTimer(self)
        self._mirror_timer.timeout.connect(lambda: self.refresh_mirrors())

        self.setWindowTitle(APP_TITLE)
        self.resize(1380, 880)
        self.setMinimumSize(1080, 700)
        theme.enable_dark_title_bar(self)
        self._center_on_screen()
        QTimer.singleShot(0, self.startup)

    # ---------------------------------------------------------------- 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(self)
        self.sidebar.navigate.connect(self._on_nav)
        body.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.topbar = TopBar(self)
        self.topbar.search_submitted.connect(self.open_search)
        self.topbar.back_requested.connect(self.go_back)
        self.topbar.mirror_clicked.connect(lambda: self.refresh_mirrors(force=True))
        right.addWidget(self.topbar)

        self.stack = QStackedWidget(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.pages = self._pages
        self._pages["home"] = HomePage(self.ctx, self)
        self._pages["search"] = SearchPage(self.ctx, self)
        self._pages["browse"] = BrowsePage(self.ctx, self)
        self._pages["detail"] = DetailPage(self.ctx, self)
        self._pages["downloads"] = DownloadsPage(self.ctx, self)
        self._pages["settings"] = SettingsPage(self.ctx, self)
        self._pages["player"] = PlayerPage(self.ctx, self)
        for page in self._pages.values():
            self.stack.addWidget(page)

        self.toast = Toast(self)
        self.ctx.toast = self.toast

        self.sidebar.set_active("home")
        self._entries.append({"name": "home", "kwargs": {}})
        self.stack.setCurrentWidget(self._pages["home"])

    def _build_shortcuts(self) -> None:
        focus_search = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_search.activated.connect(self._focus_search)
        back = QShortcut(QKeySequence("Alt+Left"), self)
        back.activated.connect(self.go_back)
        home = QShortcut(QKeySequence("Ctrl+H"), self)
        home.activated.connect(self.go_home)
        downloads = QShortcut(QKeySequence("Ctrl+J"), self)
        downloads.activated.connect(self.open_downloads)
        refresh = QShortcut(QKeySequence("F5"), self)
        refresh.activated.connect(lambda: self.refresh_mirrors(force=True))

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # ---------------------------------------------------------------- 启动
    def startup(self) -> None:
        self.apply_settings()
        QTimer.singleShot(1400, lambda: self.refresh_mirrors(force=True, initial=True))
        if not self.settings.onboarded:
            QTimer.singleShot(420, self.show_onboarding)

    # ---------------------------------------------------------------- 新手指引
    def show_onboarding(self) -> None:
        """首次启动的免责声明 + 小贴士；设置页可以再手动打开。"""
        if self._onboarding is not None:
            self._onboarding.deleteLater()
            self._onboarding = None
        overlay = OnboardingOverlay(self)
        overlay.finished.connect(self._on_onboarding_finished)
        overlay.declined.connect(self.close)
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()
        self._onboarding = overlay
        overlay.start()

    def _on_onboarding_finished(self) -> None:
        self.settings.onboarded = True
        self.ctx.save()
        if self._onboarding is not None:
            self._onboarding.deleteLater()
            self._onboarding = None

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._onboarding is not None:
            self._onboarding.setGeometry(self.rect())

    # ---------------------------------------------------------------- 线路
    def refresh_mirrors(self, force: bool = False, on_done: Optional[Callable[[], None]] = None, initial: bool = False) -> None:
        if self._mirror_busy:
            if on_done is not None:
                on_done()
            return
        self._mirror_busy = True
        self.sidebar.set_measuring()
        if initial:
            self.topbar.set_mirror_text("正在测速…")
        preferred = self.settings.mirror_override or self.store.last_mirror
        extra = list(self.store.mirrors)

        def work() -> MirrorReport:
            return measure_mirrors(preferred=preferred, extra=extra)

        def done(report: MirrorReport) -> None:
            self._mirror_busy = False
            self.ctx.mirror = report
            if on_done is not None:
                on_done()
            if report.base_url:
                changed = report.base_url != self.api.base
                if not self.settings.mirror_override:
                    self.api.set_base(report.base_url)
                    self.store.last_mirror = report.base_url
                self.store.set_mirrors([base for base, _ in report.alive], self.api.base)
                self.ctx.save()
                self._apply_mirror_ui(report)
                home = self._pages.get("home")
                if home is None or getattr(home, "_data", None) is None:
                    self._reload_current_page()
            else:
                self.sidebar.set_mirror("", None, 0, report.checked)
                self.topbar.set_mirror_text("线路不可用")
                self.ctx.notify(report.error or tr("没有可用线路"), "alert", theme.ACCENT)

        def failed(message: str) -> None:
            self._mirror_busy = False
            if on_done is not None:
                on_done()
            self.sidebar.set_mirror("", None, 0, 0)
            self.topbar.set_mirror_text("测速失败")
            log.warning("镜像测速失败: %s", message)

        self.tasks.run(work, on_done=done, on_error=failed)

    def _apply_mirror_ui(self, report: MirrorReport) -> None:
        base = self.settings.mirror_override or report.base_url or ""
        latency = report.latency_ms
        for candidate, value in report.alive:
            if candidate == base:
                latency = value
                break
        self.sidebar.set_mirror(base, latency, len(report.alive), report.checked)
        host = base.replace("https://", "").replace("http://", "").rstrip("/")
        self.topbar.set_mirror_text(
            tr("{host} · {ms} ms", host=host, ms=int(latency or 0)) if base else tr("线路不可用")
        )

    def set_mirror(self, base: str) -> None:
        self.settings.mirror_override = base
        self.ctx.save()
        if base:
            self.api.set_base(base)
            self.store.last_mirror = base
            latency: Optional[float] = None
            alive = total = 0
            if self.ctx.mirror is not None:
                total = self.ctx.mirror.checked
                alive = len(self.ctx.mirror.alive)
                for candidate, value in self.ctx.mirror.alive:
                    if candidate == base:
                        latency = value
                        break
            host = base.replace("https://", "").replace("http://", "").rstrip("/")
            self.topbar.set_mirror_text(
                tr("{host} · {ms} ms", host=host, ms=int(latency)) if latency else host
            )
            self.sidebar.set_mirror(base, latency, alive, total)
        elif self.ctx.mirror and self.ctx.mirror.base_url:
            self.api.set_base(self.ctx.mirror.base_url)
            self.sidebar.set_mirror(
                self.ctx.mirror.base_url,
                self.ctx.mirror.latency_ms,
                len(self.ctx.mirror.alive),
                self.ctx.mirror.checked,
            )
        host = base.replace("https://", "").rstrip("/")
        self.ctx.notify(
            tr("已切换到 {name}", name=host if base else tr("自动最快线路")),
            "check",
            theme.GREEN,
        )
        self._reload_current_page()

    def _on_api_failover(self) -> None:
        """在下载/解析线程里被调用：同步换线路，再通知界面。"""
        report = self.ctx.mirror
        current = self.api.base
        candidate = ""
        if report is not None:
            for base, _ in report.alive:
                if base != current:
                    candidate = base
                    break
        if not candidate:
            for base in FALLBACK_MIRRORS:
                if base != current:
                    candidate = base
                    break
        if candidate:
            self.api.set_base(candidate)
            self.store.last_mirror = candidate
            self.mirror_switched.emit(candidate)

    def _on_mirror_switched(self, base: str) -> None:
        host = base.replace("https://", "").rstrip("/")
        self.topbar.set_mirror_text(host)
        self.ctx.notify(tr("原线路无响应，已自动切换到 {host}", host=host), "alert", theme.GOLD)
        self.sidebar.set_mirror(base, None, len(self.ctx.mirror.alive) if self.ctx.mirror else 0, 0)

    # ---------------------------------------------------------------- 导航
    def _on_nav(self, key: str) -> None:
        if key == "home":
            self.go_home()
        elif key == "downloads":
            self.open_downloads()
        elif key == "settings":
            self.open_settings()
        elif key.startswith("type:"):
            type_id = int(key.split(":", 1)[1])
            name = next((n for n, t in CATEGORIES if t == type_id), tr("分类"))
            self.open_category(type_id, name)

    def _navigate(self, name: str, push: bool = True, **kwargs) -> None:
        if name != "player" and self._immersive:
            if self._cinema:
                self.set_cinema(False)
            self.set_immersive(False)
        page = self._pages[name]
        current = self.stack.currentWidget()
        if current is not None and current is not page and hasattr(current, "on_hide"):
            try:
                current.on_hide()
            except Exception:  # noqa: BLE001
                log.debug("页面收尾失败 %s", current, exc_info=True)
        if push:
            self._entries.append({"name": name, "kwargs": kwargs})
        snapshot = self._grab_page(current) if (name != "player" and current is not page) else None
        self._changing_page = True
        self.stack.setCurrentWidget(page)
        self._changing_page = False
        if snapshot is not None:
            self._crossfade(page, snapshot)
        if hasattr(page, "on_show"):
            page.on_show(**kwargs)
        self._sync_nav(name, kwargs)

    def _grab_page(self, page: Optional[QWidget]) -> Optional[QPixmap]:
        """切页前把旧页面拍成一张图，用于交叉淡出。"""
        if page is None or theme.motion(1) <= 0:
            return None
        try:
            if page.width() < 8 or page.height() < 8:
                return None
            return page.grab()
        except RuntimeError:
            return None

    def _crossfade(self, page: QWidget, snapshot: QPixmap) -> None:
        """旧页面截图淡出：一帧只合成一张位图，比整页不透明度动画顺。"""
        duration = theme.motion(200)
        if duration <= 0:
            return
        try:
            fade = components.SnapshotFade(self.stack, snapshot)
        except RuntimeError:
            return
        fade.start(duration)
        self._crossfades = [fade] + [item for item in getattr(self, "_crossfades", []) if item is not fade][:2]
        self._crossfades = [item for item in self._crossfades if _alive(item)]
        for item in self._crossfades:
            try:
                item.raise_()
            except RuntimeError:
                pass

    def _sync_nav(self, name: str, kwargs: dict) -> None:
        self.topbar.back_button.setVisible(len(self._entries) > 1)
        if name == "home":
            self.sidebar.set_active("home")
        elif name == "browse":
            self.sidebar.set_active("type:%s" % kwargs.get("type_id", ""))
        elif name == "downloads":
            self.sidebar.set_active("downloads")
        elif name == "settings":
            self.sidebar.set_active("settings")

    def _reset_to(self, name: str, **kwargs) -> None:
        self._entries = [{"name": name, "kwargs": kwargs}]
        self._navigate(name, push=False, **kwargs)

    def go_home(self) -> None:
        self._reset_to("home")

    def go_back(self) -> None:
        if len(self._entries) <= 1:
            return
        self._entries.pop()
        entry = self._entries[-1]
        self._navigate(entry["name"], push=False, **entry["kwargs"])

    def open_search(self, keyword: str) -> None:
        keyword = keyword.strip()
        if not keyword:
            return
        self.topbar.set_search_text(keyword)
        self._navigate("search", keyword=keyword)

    def open_category(self, type_id: int, title: str) -> None:
        self._navigate("browse", type_id=type_id, title=title)

    def open_detail(self, vod_id: str, resume=None) -> None:
        self._navigate("detail", vod_id=vod_id, resume=resume)

    def open_player(self, detail, sid: int, nid: int, position: float = 0.0) -> None:
        page = self._pages["player"]
        current = self.stack.currentWidget()
        if current is not None and current is not page and hasattr(current, "on_hide"):
            try:
                current.on_hide()
            except Exception:  # noqa: BLE001
                log.debug("页面收尾失败", exc_info=True)
        # 续播只生效一次：播放页已经起播，返回详情时不该再自动跳回播放页
        for entry in reversed(self._entries):
            if entry.get("name") == "detail":
                entry.get("kwargs", {}).pop("resume", None)
                break
        self._entries.append(
            {"name": "player", "kwargs": {"detail": detail, "sid": sid, "nid": nid, "position": position}}
        )
        self.stack.setCurrentWidget(page)
        page.open(detail, sid, nid, position)
        self.sidebar.set_active("")
        self.topbar.search.setPlaceholderText("搜索动漫名称、角色或年份")
        self.set_immersive(True)
        page.setFocus(Qt.OtherFocusReason)

    def leave_player(self) -> None:
        if self._cinema:
            self.set_cinema(False)
        self.set_immersive(False)
        if len(self._entries) > 1 and self._entries[-1]["name"] == "player":
            self._entries.pop()
        entry = self._entries[-1]
        # 从播放页退回详情时，详情不能拿续播参数再把自己弹回播放页
        if entry.get("name") == "detail":
            entry.setdefault("kwargs", {}).pop("resume", None)
        self._navigate(entry["name"], push=False, **entry["kwargs"])

    def open_downloads(self) -> None:
        self._navigate("downloads")

    def open_settings(self) -> None:
        self._navigate("settings")

    def _reload_current_page(self) -> None:
        name = self._entries[-1]["name"] if self._entries else "home"
        if name in ("home", "browse", "search", "detail"):
            entry = self._entries[-1]
            page = self._pages[name]
            if hasattr(page, "on_show"):
                page.on_show(**entry["kwargs"])

    # ---------------------------------------------------------------- 全屏
    def set_cinema(self, cinema: bool) -> None:
        if cinema == self._cinema:
            return
        self._cinema = cinema
        if cinema:
            self._normal_geometry = self.geometry()
            self.sidebar.hide()
            self.topbar.hide()
            self.showFullScreen()
            self._pages["player"].set_cinema_state(True)
        else:
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self.sidebar.setVisible(not self._immersive)
            self.topbar.setVisible(not self._immersive)
            self._pages["player"].set_cinema_state(False)
        self._pages["player"]._layout_children()

    # ---------------------------------------------------------------- 播放沉浸
    def set_immersive(self, on: bool) -> None:
        """播放页独占窗口：收起侧栏与顶栏，回到浏览页再恢复。"""
        if on == self._immersive:
            return
        self._immersive = on
        if not self._cinema:
            self.sidebar.setVisible(not on)
            self.topbar.setVisible(not on)
        page = self._pages.get("player")
        if page is not None:
            page._layout_children()

    # ---------------------------------------------------------------- 下载事件
    def _on_download_progress(self, progress: DownloadProgress) -> None:
        page = self._pages.get("downloads")
        if isinstance(page, DownloadsPage):
            page.on_progress(progress)
        self._on_download_count_changed()

    def _on_download_count_changed(self) -> None:
        active = 0
        for job in list(self.downloads._jobs.values()):  # noqa: SLF001 - 只读计数
            if job.progress.active:
                active += 1
        button = self.sidebar.buttons.get("downloads")
        if button is not None:
            self.sidebar.set_download_count(active)

    # ---------------------------------------------------------------- 设置与工具
    def apply_settings(self) -> None:
        self.proxy.cache_limit = self.settings.cache_limit_mb * 1024 * 1024
        minutes = max(1, int(self.settings.refresh_minutes))
        self._mirror_timer.start(minutes * 60 * 1000)
        theme.REDUCE_MOTION = bool(self.settings.reduce_motion)
        masking.set_enabled(bool(self.settings.debug_blank))
        masking.set_keep_search(bool(self.settings.debug_keep_search))
        self.sidebar.set_categories(self.settings.categories)
        browse = self._pages.get("browse")
        if browse is not None:
            browse.on_cache_setting_changed()

    def apply_debug_mode(self) -> None:
        """调试遮蔽开关变化后，把所有缓存过的页面一起作废。"""
        masking.set_enabled(bool(self.settings.debug_blank))
        masking.set_keep_search(bool(self.settings.debug_keep_search))
        home = self._pages.get("home")
        if home is not None:
            home._data = None  # noqa: SLF001 - 首页缓存需要整体失效
            reload_home = getattr(home, "reload", None)
            if callable(reload_home):
                try:
                    reload_home()
                except Exception:  # noqa: BLE001
                    log.debug("首页重载失败", exc_info=True)
        downloads = self._pages.get("downloads")
        if downloads is not None:
            try:
                downloads.on_show()
            except Exception:  # noqa: BLE001
                log.debug("下载页重载失败", exc_info=True)
        current = self.stack.currentWidget()
        for name in ("search", "browse", "detail"):
            page = self._pages.get(name)
            invalidate = getattr(page, "invalidate", None)
            if not callable(invalidate):
                continue
            try:
                invalidate(reload_now=page is current)
            except Exception:  # noqa: BLE001
                log.debug("页面作废失败 %s", name, exc_info=True)
        if current is not None and current not in (
            self._pages.get("search"),
            self._pages.get("browse"),
            self._pages.get("detail"),
        ):
            self._reload_current_page()

    def apply_language(self, code: str) -> None:
        """切换界面语言：静态文案就地重译，动态内容整页重建。"""
        if code not in {item[0] for item in i18n.available()}:
            code = i18n.DEFAULT_LANGUAGE
        if self.ctx.settings.language != code:
            self.ctx.settings.language = code
            self.ctx.save()
        i18n.set_language(code, notify=False)
        i18n.retranslate(self)
        self._rebuild_dynamic_pages()
        self.restyle()
        self.update()

    def _rebuild_dynamic_pages(self) -> None:
        """语言变了：缓存过的列表、集数、下载行都要按新语言重新生成一遍。"""
        home = self._pages.get("home")
        if home is not None:
            home._data = None  # noqa: SLF001
            reload_home = getattr(home, "reload", None)
            if callable(reload_home):
                try:
                    reload_home()
                except Exception:  # noqa: BLE001
                    log.debug("首页重载失败", exc_info=True)
        current = self.stack.currentWidget()
        for name in ("search", "browse", "detail"):
            page = self._pages.get(name)
            invalidate = getattr(page, "invalidate", None)
            if callable(invalidate):
                try:
                    invalidate(reload_now=page is current)
                except Exception:  # noqa: BLE001
                    log.debug("页面作废失败 %s", name, exc_info=True)
        downloads = self._pages.get("downloads")
        if downloads is not None:
            try:
                downloads.on_show()
            except Exception:  # noqa: BLE001
                log.debug("下载页重载失败", exc_info=True)
        settings_page = self._pages.get("settings")
        if settings_page is not None:
            try:
                settings_page.on_show()
            except Exception:  # noqa: BLE001
                log.debug("设置页刷新失败", exc_info=True)
        player = self._pages.get("player")
        refresh = getattr(player, "refresh_theme", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001
                log.debug("播放页刷新失败", exc_info=True)

    def apply_theme_mode(self, mode: str) -> None:
        """切换深色/浅色：写回设置、重刷 QSS、图标与自绘控件。"""
        app = QApplication.instance()
        if app is None:
            return
        mode = mode if mode in ("dark", "light") else "dark"
        if self.ctx.settings.theme == mode and theme.MODE == mode:
            return
        snapshot = self._grab_window()
        self.ctx.settings.theme = mode
        self.ctx.save()
        if not theme.apply_mode(app, mode):
            self.restyle()
            theme.enable_dark_title_bar(self)
            return
        self.restyle()
        theme.enable_dark_title_bar(self)
        self.update()
        self._crossfade_window(snapshot)

    def apply_accent(self, color: str) -> None:
        """自定义强调色：只改当前这套配色，写回设置并重刷整套样式。"""
        mode = theme.MODE if theme.MODE in ("dark", "light") else "dark"
        color = (color or "").strip()
        self.ctx.settings.theme = mode
        if mode == "dark":
            self.ctx.settings.accent_dark = color
        else:
            self.ctx.settings.accent_light = color
        self.ctx.save()
        snapshot = self._grab_window()
        theme.set_accent(mode, color)
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app)
        self.restyle()
        self.update()
        self._crossfade_window(snapshot)

    def _grab_window(self) -> Optional[QPixmap]:
        try:
            if theme.motion(1) <= 0 or self.width() < 8:
                return None
            return self.grab()
        except RuntimeError:
            return None

    def _crossfade_window(self, snapshot: Optional[QPixmap]) -> None:
        if snapshot is None:
            return
        duration = theme.motion(220)
        if duration <= 0:
            return
        try:
            fade = components.SnapshotFade(self, snapshot)
        except RuntimeError:
            return
        fade.setGeometry(self.rect())
        fade.start(duration)

    def restyle(self) -> None:
        components.refresh_icons(self)
        self.sidebar.refresh_theme()
        self.topbar.refresh_theme()
        if self._onboarding is not None:
            try:
                self._onboarding.refresh_theme()
            except RuntimeError:
                self._onboarding = None
        for page in self._pages.values():
            refresh = getattr(page, "refresh_theme", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001
                    log.debug("页面主题刷新失败", exc_info=True)
        self.update()

    def _focus_search(self) -> None:
        self.topbar.search.setFocus(Qt.OtherFocusReason)
        self.topbar.search.selectAll()

    def open_in_explorer(self, path) -> None:
        target = Path(path)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            if target.is_dir():
                os.startfile(str(target))  # noqa: S606 - Windows 资源管理器
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
        except OSError as exc:
            log.warning("打开目录失败 %s: %s", target, exc)
            self.ctx.notify(tr("打开目录失败：{error}", error=exc), "alert", theme.ACCENT)

    # ---------------------------------------------------------------- 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        player = self._pages.get("player")
        if isinstance(player, PlayerPage):
            try:
                player.on_hide()
            except Exception:  # noqa: BLE001
                pass
            try:
                player.preloader.close()
            except Exception:  # noqa: BLE001
                pass
        self.settings.last_page = "home"
        self.store.settings = self.settings
        self.ctx.save()
        try:
            self.downloads.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proxy.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.images.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tasks.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
