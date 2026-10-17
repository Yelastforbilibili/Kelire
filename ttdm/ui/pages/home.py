"""首页：推荐大图轮播 + 继续观看 + 各分区横滑行。"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import masking
from ...core.site import CATEGORIES, HomeData, PosterItem
from ...i18n import tr
from .. import icons, theme
from ..components import (
    BusyOverlay,
    EmptyState,
    RowScroller,
    SectionHeader,
    TagLabel,
    make_button,
    fade_in,
)

HERO_HEIGHT = 320


class _Dot(QWidget):
    clicked = Signal(int)

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.active = False
        self.setFixedSize(22, 12)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, value: bool) -> None:
        self.active = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(0, 4, 22, 4), 2, 2)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 70))
            painter.drawEllipse(QRectF(7, 4, 7, 4).adjusted(0, 0, 0, 0))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)


class HeroBanner(QWidget):
    """大图推荐位：背景封面 + 左侧渐变 + 标题信息，自动轮播。"""

    activated = Signal(str)

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._items: list[PosterItem] = []
        self._index = 0
        self._image: Optional[QImage] = None
        self._prev_image: Optional[QImage] = None
        self._fade = 1.0
        self._fade_anim: Optional[QVariantAnimation] = None
        self._url = ""
        self.setMinimumHeight(HERO_HEIGHT)
        self.setMaximumHeight(HERO_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        self.textbox = QWidget(self)
        self.textbox.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        box = QVBoxLayout(self.textbox)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addStretch(1)

        self.eyebrow = QLabel("今日推荐")
        self.eyebrow.setObjectName("Eyebrow")
        box.addWidget(self.eyebrow)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(
            "QLabel { font-size: 34px; font-weight: 800; color: #FFFFFF; }"
        )
        box.addWidget(self.title)

        self.meta = QLabel("")
        self.meta.setStyleSheet("QLabel { color: rgba(237,239,243,190); font-size: 12.5px; }")
        box.addWidget(self.meta)

        self.tags = QWidget()
        self.tags.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.tags_row = QHBoxLayout(self.tags)
        self.tags_row.setContentsMargins(0, 0, 0, 0)
        self.tags_row.setSpacing(8)
        self.tags_row.addStretch(1)
        box.addWidget(self.tags)

        self.actions = QWidget(self)
        self.actions.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.actions_row = QHBoxLayout(self.actions)
        self.actions_row.setContentsMargins(0, 4, 0, 0)
        self.actions_row.setSpacing(10)
        self.play_button = make_button("查看详情", primary=True, icon_name="play", height=38)
        self.play_button.setCursor(Qt.PointingHandCursor)
        self.play_button.clicked.connect(lambda: self.activated.emit(self._current_id()))
        self.play_button.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.actions_row.addWidget(self.play_button)
        self.actions_row.addStretch(1)
        box.addWidget(self.actions)

        self._dots: list[_Dot] = []
        self._counter = QLabel("")
        self._counter.setStyleSheet("QLabel { color: rgba(237,239,243,150); font-size: 11px; letter-spacing: 1px; }")
        self._counter.setParent(self)

        self._timer = QTimer(self)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(lambda: self._step(1))

    # ---------- 数据 ----------
    def set_items(self, items: list[PosterItem]) -> None:
        self._items = list(items)[:8]
        for dot in self._dots:
            dot.deleteLater()
        self._dots.clear()
        for index in range(len(self._items)):
            dot = _Dot(index, self)
            dot.clicked.connect(self.show_index)
            dot.show()
            self._dots.append(dot)
        self._index = 0
        self._apply()
        self._layout_dots()
        if len(self._items) > 1:
            self._timer.start()
        else:
            self._timer.stop()

    def _current_id(self) -> str:
        if not self._items:
            return ""
        return self._items[self._index].vod_id

    def show_index(self, index: int) -> None:
        if not self._items:
            return
        self._index = index % len(self._items)
        self._apply()

    def _step(self, delta: int) -> None:
        if len(self._items) <= 1:
            return
        self._index = (self._index + delta) % len(self._items)
        self._apply()

    def _apply(self) -> None:
        if not self._items:
            return
        item = self._items[self._index]
        self.title.setText(item.name)
        meta = [part for part in (item.year, item.region, item.note) if part]
        self.meta.setText("  ·  ".join(meta))
        for index in reversed(range(self.tags_row.count() - 1)):
            widget = self.tags_row.itemAt(index).widget()
            if widget is not None:
                widget.deleteLater()
        position = 0
        for genre in (item.genres or [item.category])[:4]:
            if not genre:
                continue
            chip = TagLabel(genre, theme.TEXT, 26)
            self.tags_row.insertWidget(position, chip)
            position += 1
        self._counter.setText("%02d / %02d" % (self._index + 1, len(self._items)))
        for index, dot in enumerate(self._dots):
            dot.set_active(index == self._index)
        self._load_cover(item.cover)

    def _load_cover(self, url: str) -> None:
        if not url:
            if self._url:
                self._url = ""
                self._image = None
                self._prev_image = None
                self._fade = 1.0
                self.update()
            return
        if url == self._url:
            return
        self._url = url
        image = self.ctx.images.request(url)
        if image is not None:
            self._set_image(image)
            return

        def on_ready(ready_url: str, ready_image: QImage) -> None:
            if ready_url != url:
                return
            try:
                self._set_image(ready_image)
            except RuntimeError:
                pass

        self.ctx.images.subscribe(url, on_ready)

    def _set_image(self, image: QImage) -> None:
        """换封面时保留上一张，用 320ms 交叉淡入，避免幻灯片硬切。"""
        previous = self._image
        self._image = image
        duration = theme.motion(320)
        if previous is not None and not previous.isNull() and duration > 0:
            self._prev_image = previous
            self._fade = 0.0
            anim = QVariantAnimation(self)
            anim.setDuration(duration)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(self._on_fade)
            anim.finished.connect(self._finish_fade)
            anim.start(QVariantAnimation.DeleteWhenStopped)
            self._fade_anim = anim
        else:
            self._prev_image = None
            self._fade = 1.0
        self.update()

    def _on_fade(self, value) -> None:
        self._fade = float(value)
        self.update()

    def _finish_fade(self) -> None:
        self._fade = 1.0
        self._prev_image = None
        self.update()

    # ---------- 绘制与交互 ----------
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if len(self._items) > 1:
            self._timer.start()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            vod_id = self._current_id()
            if vod_id:
                self.activated.emit(vod_id)
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        margin = 40
        self.textbox.setGeometry(margin, 0, max(280, min(640, self.width() - margin * 2 - 180)), self.height())
        self._layout_dots()

    def _layout_dots(self) -> None:
        """轮播点与计数靠右下角排列；控件建好后要主动调一次。"""
        margin = 40
        total = len(self._dots)
        dot_width = 22
        gap = 8
        if total:
            start = self.width() - margin - total * dot_width - (total - 1) * gap
            for index, dot in enumerate(self._dots):
                dot.move(max(start, margin) + index * (dot_width + gap), self.height() - 30)
        self._counter.adjustSize()
        self._counter.move(
            self.width() - margin - self._counter.width() - total * (dot_width + gap) - 16,
            self.height() - 31,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        painter.setClipPath(path)
        painter.fillRect(rect, QColor("#111620"))

        def draw_cover(source: QImage, opacity: float) -> None:
            scaled = source.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            offset_x = (self.width() - scaled.width()) / 2
            offset_y = (self.height() - scaled.height()) / 2
            painter.setOpacity(opacity)
            painter.drawImage(int(offset_x), int(offset_y), scaled)
            painter.setOpacity(1.0)

        if self._prev_image is not None and not self._prev_image.isNull():
            draw_cover(self._prev_image, 1.0)
        if self._image is not None and not self._image.isNull():
            draw_cover(self._image, self._fade)

        shade = QLinearGradient(rect.left(), 0, rect.width() * 0.9, 0)
        shade.setColorAt(0.0, QColor(8, 10, 15, 246))
        shade.setColorAt(0.45, QColor(8, 10, 15, 205))
        shade.setColorAt(0.78, QColor(8, 10, 15, 90))
        shade.setColorAt(1.0, QColor(8, 10, 15, 30))
        painter.fillRect(rect, shade)

        bottom = QLinearGradient(0, rect.height() * 0.55, 0, rect.height())
        bottom.setColorAt(0.0, QColor(8, 10, 15, 0))
        bottom.setColorAt(1.0, QColor(8, 10, 15, 170))
        painter.fillRect(rect, bottom)

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class _HistoryCard:
    """继续观看卡片的数据载体（附带自定义副标题）。"""

    def __init__(self, vod_id: str, name: str, cover: str, subtitle: str) -> None:
        self.vod_id = vod_id
        self.name = name
        self.cover = cover
        self.subtitle = subtitle
        self.note = ""


class HomePage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self._loaded_at = 0.0
        self._data: Optional[HomeData] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)

        self.content = QWidget()
        self.scroll.setWidget(self.content)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(34, 24, 34, 44)
        self.body.setSpacing(30)

        self.hero = HeroBanner(ctx, self.content)
        self.hero.activated.connect(lambda vod_id: shell.open_detail(vod_id))
        self.body.addWidget(self.hero)

        self.continue_box = QWidget(self.content)
        self.continue_layout = QVBoxLayout(self.continue_box)
        self.continue_layout.setContentsMargins(0, 0, 0, 0)
        self.continue_layout.setSpacing(12)
        self.continue_header = SectionHeader("继续观看", "上次看到的位置")
        self.continue_layout.addWidget(self.continue_header)
        self.continue_row = RowScroller()
        self.continue_row.setMinimumHeight(280)
        self.continue_row.activated.connect(self._on_history_clicked)
        self.continue_layout.addWidget(self.continue_row)
        self.continue_box.hide()
        self.body.addWidget(self.continue_box)

        self.sections_host = QWidget(self.content)
        self.sections_layout = QVBoxLayout(self.sections_host)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(26)
        self.body.addWidget(self.sections_host)

        self.status = EmptyState("正在获取最新推荐", "首次启动会先探测可用线路，稍等一下", "sparkle", self.content)
        self.body.addWidget(self.status)
        self.status.hide()
        self.busy = BusyOverlay(self.content, "正在加载首页")
        self.hero.hide()
        self.sections_host.hide()
        self._error_label = ""

    # ---------- 生命周期 ----------
    def on_show(self, **kwargs) -> None:
        if self._loaded_at <= 0:
            self.reload()
        elif time.time() - self._loaded_at > 900:
            self.reload()
        self._refresh_history()

    def reload(self) -> None:
        if self._data is None:
            self.busy.start("正在加载首页")
        self.status.hide()
        self.ctx.tasks.run(self._fetch, self._on_loaded, self._on_error)

    def _fetch(self) -> HomeData:
        return self.ctx.api.home()

    def _on_error(self, message: str) -> None:
        self.busy.stop()
        self._error_label = message
        self.status.title.setText("首页加载失败")
        self.status.description.setText(message)
        self.status.show()
        self.ctx.notify(message, "alert", theme.ACCENT)

    def _on_loaded(self, data: HomeData) -> None:
        self.busy.stop()
        self._data = data
        self._loaded_at = time.time()
        self.status.hide()
        if data.featured:
            self.hero.set_items(data.featured)
            self.hero.show()
        else:
            self.hero.hide()

        self._clear_sections()
        for title, items in data.sections:
            if not items:
                continue
            self.sections_layout.addWidget(self._build_section(title, items))
        self.sections_host.show()
        fade_in(self.sections_host, 240)
        self._refresh_history()

    # ---------- 构建 ----------
    def _clear_sections(self) -> None:
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_section(self, title: str, items: list[PosterItem]) -> QWidget:
        holder = QWidget(self.sections_host)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        category = next((pair for pair in CATEGORIES if pair[0] == title.strip()), None)
        header = SectionHeader(title, tr("{n} 部", n=len(items)), "查看全部" if category else "")
        if category:
            header.more_clicked.connect(
                lambda _=False, type_id=category[1], name=category[0]: self.shell.open_category(type_id, name)
            )
        layout.addWidget(header)
        row = RowScroller()
        row.setMinimumHeight(280)
        row.set_items(items, self.ctx.images)
        row.activated.connect(lambda vod_id: self.shell.open_detail(vod_id))
        layout.addWidget(row)
        return holder

    # ---------- 继续观看 ----------
    def _refresh_history(self) -> None:
        entries = list(self.ctx.store.history)[:12]
        if not entries:
            self.continue_box.hide()
            return
        cards = []
        for entry in entries:
            episode = entry.episode or tr("第{ep}集", ep=entry.nid)
            subtitle = episode
            if entry.position and entry.position > 5:
                from ..components import fmt_duration

                subtitle = tr("{episode} · 看到 {position}", episode=episode, position=fmt_duration(entry.position))
            cards.append(
                _HistoryCard(
                    entry.vod_id,
                    masking.name(entry.name),
                    masking.cover(entry.cover, entry.name),
                    subtitle,
                )
            )
        self.continue_row.set_items(cards, self.ctx.images)
        self.continue_box.show()

    def _on_history_clicked(self, vod_id: str) -> None:
        entry = self.ctx.store.get_history(vod_id)
        if entry is None:
            self.shell.open_detail(vod_id)
            return
        self.shell.open_detail(vod_id, resume=entry)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.busy.setGeometry(self.rect())
