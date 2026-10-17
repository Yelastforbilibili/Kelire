"""播放页：本地 HLS 代理 + 自定义绘制视频画布 + 覆盖式控件。

原站在线播放是单连接顺序拉流，拖动进度条非常卡。这里用 HlsProxy 把整集
分片改写成本机 VOD 清单，播放器只读 127.0.0.1，预取线程会把后面几十段
提前拉好，拖动基本等于读本地文件。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.hls import Segment, resolve_playlist
from ...core.http import MediaClient
from ...core.preload import (
    STATE_DONE,
    STATE_ERROR,
    STATE_QUEUED,
    STATE_RUNNING,
    PreloadManager,
    PreloadUpdate,
)
from ...core.site import AnimeDetail, Episode, PlayInfo
from ...i18n import tr
from .. import icons, theme
from ..components import IconButton, Spinner, elide, fmt_duration

SEEK_STEP = 5000
CONTROL_HIDE_MS = 2800


class VideoCanvas(QWidget):
    """把 QVideoFrame 直接画到控件上，控件才能和覆盖层合成。"""

    mouse_moved = Signal()
    double_clicked = Signal()
    key_pressed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._image: Optional[QImage] = None
        self._scaled: Optional[QImage] = None
        self._scaled_for: QSize | None = None
        self.setStyleSheet("background-color: #05070A;")

    def _on_frame(self, frame) -> None:
        if not self.isVisible():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._image = image
        self.update()

    def clear(self) -> None:
        self._image = None
        self._scaled = None
        self._scaled_for = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#05070A"))
        if self._image is None:
            painter.end()
            return
        target = self._fit_rect(self._image.size())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, self._image)
        painter.end()

    def _fit_rect(self, image_size: QSize) -> QRectF:
        width = max(1, self.width())
        height = max(1, self.height())
        scale = min(width / image_size.width(), height / image_size.height())
        draw_w = image_size.width() * scale
        draw_h = image_size.height() * scale
        return QRectF((width - draw_w) / 2.0, (height - draw_h) / 2.0, draw_w, draw_h)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.mouse_moved.emit()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.key_pressed.emit(event)
        if not event.isAccepted():
            super().keyPressEvent(event)


class BufferBar(QWidget):
    """贴在进度条下面的缓冲条：已经拉好的片段画成白色，像平时看视频的缓冲进度。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._ranges: list[tuple[float, float]] = []
        self._duration = 0.0

    def set_buffer(self, ranges: list, duration: float) -> None:
        ranges = list(ranges or [])
        duration = max(0.0, float(duration or 0.0))
        if duration == self._duration and ranges == self._ranges:
            return
        self._ranges = ranges
        self._duration = duration
        self.update()

    def clear(self) -> None:
        self.set_buffer([], 0.0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        width, height = self.width(), self.height()
        bar_h = 3.0
        top = (height - bar_h) / 2.0
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRoundedRect(QRectF(0.0, top, width, bar_h), 1.5, 1.5)
        if self._duration > 0 and self._ranges:
            painter.setBrush(QColor(255, 255, 255, 216))
            for start, end in self._ranges:
                x0 = max(0.0, min(1.0, start / self._duration)) * width
                x1 = max(0.0, min(1.0, end / self._duration)) * width
                if x1 <= x0:
                    continue
                painter.drawRoundedRect(QRectF(x0, top, max(1.0, x1 - x0), bar_h), 1.5, 1.5)
        painter.end()


class _Overlay(QWidget):
    """透明覆盖层：顶部信息条 + 底部控制台。"""

    mouse_moved = Signal()
    double_clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        top = QLinearGradient(0, 0, 0, 150)
        top.setColorAt(0.0, QColor(4, 6, 9, 205))
        top.setColorAt(1.0, QColor(4, 6, 9, 0))
        painter.fillRect(0, 0, self.width(), 150, top)
        bottom = QLinearGradient(0, self.height() - 210, 0, self.height())
        bottom.setColorAt(0.0, QColor(4, 6, 9, 0))
        bottom.setColorAt(1.0, QColor(4, 6, 9, 225))
        painter.fillRect(0, self.height() - 210, self.width(), 210, bottom)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.mouse_moved.emit()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def set_opacity(self, value: float) -> None:
        self._effect.setOpacity(max(0.0, min(1.0, value)))


class EpisodeRow(QWidget):
    """剧集行：悬停时鼠标像手电筒一样照亮局部，右侧渐显「+」用来预加载。

    预加载进度直接铺在这一行上（从右往左），文字永远画在进度条前面。
    """

    activated = Signal(int)
    preload_toggled = Signal(int)
    download_requested = Signal(int)

    HEIGHT = 40
    PLUS = 22.0
    GLOW_RADIUS = 104.0

    def __init__(self, nid: int, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.nid = int(nid)
        self._label = label
        self._current = False
        self._state = "idle"
        self._percent = 0.0
        self._hover = 0.0
        self._mouse = QPointF(-999.0, -999.0)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)

    # ---------- 状态 ----------
    def set_current(self, current: bool) -> None:
        current = bool(current)
        if current == self._current:
            return
        self._current = current
        if current:
            self._state = "idle"
            self._percent = 0.0
        self.setCursor(Qt.ArrowCursor if current else Qt.PointingHandCursor)
        self.update()

    def set_preload(self, state: str, percent: float = 0.0) -> None:
        state = state or "idle"
        percent = max(0.0, min(1.0, float(percent or 0.0)))
        if state == self._state and abs(percent - self._percent) < 0.004:
            return
        self._state = state
        self._percent = percent
        self.update()

    def _selected(self) -> bool:
        return self._state in (STATE_QUEUED, STATE_RUNNING, STATE_DONE, STATE_ERROR)

    def _plus_rect(self) -> QRectF:
        return QRectF(
            self.width() - 10.0 - self.PLUS,
            (self.height() - self.PLUS) / 2.0,
            self.PLUS,
            self.PLUS,
        )

    def _plus_alpha(self) -> float:
        if self._selected():
            return 1.0
        if self._hover <= 0.01:
            return 0.0
        # 鼠标移行尾才渐显「+」：像手电筒扫到右边
        start = max(1, self.width()) - 160.0
        proximity = max(0.0, min(1.0, (self._mouse.x() - start) / 80.0))
        return max(0.0, min(1.0, self._hover * proximity))

    # ---------- 交互 ----------
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._animate_hover(0.0)
        self._mouse = QPointF(-999.0, -999.0)
        super().leaveEvent(event)

    def _animate_hover(self, target: float) -> None:
        duration = theme.motion(150)
        self._hover_anim.stop()
        if duration <= 0:
            self._on_hover(target)
            return
        self._hover_anim.setDuration(duration)
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._mouse = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if not self.rect().contains(event.position().toPoint()):
            super().mouseReleaseEvent(event)
            return
        point = event.position()
        if event.button() == Qt.RightButton:
            self.download_requested.emit(self.nid)
        elif event.button() == Qt.LeftButton:
            if not self._current and self._plus_alpha() > 0.2 and self._plus_rect().adjusted(-4, -4, 4, 4).contains(point):
                self.preload_toggled.emit(self.nid)
            else:
                self.activated.emit(self.nid)
        super().mouseReleaseEvent(event)

    # ---------- 绘制 ----------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        width, height = self.width(), self.height()
        if width <= 4 or height <= 4:
            painter.end()
            return
        rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        hover = max(0.0, min(1.0, self._hover))

        base = QColor(theme.ACCENT if self._current else "#FFFFFF")
        if self._current:
            base.setAlphaF(0.90)
        else:
            base.setAlphaF(0.045 + 0.035 * hover)
        painter.fillPath(path, base)

        # 预加载进度：从右往左铺
        if not self._current and self._selected():
            if self._state == STATE_DONE:
                progress = 1.0
            elif self._state == STATE_QUEUED:
                progress = 0.0
            else:
                progress = self._percent
            if progress > 0.002:
                painter.save()
                painter.setClipPath(path)
                bar = QColor(theme.ACCENT)
                bar.setAlphaF(0.52)
                painter.fillRect(QRectF(width * (1.0 - progress), 0.0, width * progress, height), bar)
                painter.restore()
            accent = QColor(theme.ACCENT)
            accent.setAlphaF(0.72 if self._state != STATE_ERROR else 0.35)
            painter.setPen(QPen(accent, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        # 手电筒：中心微微提亮，四周压暗
        if not self._current and hover > 0.01:
            painter.save()
            painter.setClipPath(path)
            radius = max(self.GLOW_RADIUS, height * 2.4)
            gradient = QRadialGradient(self._mouse, radius)
            gradient.setColorAt(0.0, QColor(255, 255, 255, int(46 * hover)))
            gradient.setColorAt(0.38, QColor(255, 255, 255, int(14 * hover)))
            gradient.setColorAt(0.72, QColor(2, 4, 8, int(16 * hover)))
            gradient.setColorAt(1.0, QColor(2, 4, 8, int(186 * hover)))
            painter.fillRect(self.rect(), gradient)
            painter.restore()

        # 文字（永远画在进度条前面）
        font = QFont(self.font())
        font.setPointSizeF(9.75)
        font.setBold(self._current)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF" if self._current else "#D3D9E3"))
        reserved = 84.0 if self._state in (STATE_RUNNING, STATE_DONE, STATE_ERROR) else 34.0
        text_width = max(20.0, width - 12.0 - reserved)
        painter.drawText(
            QRectF(12.0, 0.0, text_width, height),
            Qt.AlignLeft | Qt.AlignVCenter,
            elide(self._label, int(text_width), font),
        )

        if not self._current and self._state in (STATE_RUNNING, STATE_DONE):
            percent = 100 if self._state == STATE_DONE else int(round(self._percent * 100))
            painter.setPen(QColor(255, 255, 255, 172))
            painter.drawText(
                QRectF(width - 82.0, 0.0, 46.0, height),
                Qt.AlignRight | Qt.AlignVCenter,
                "%d%%" % percent,
            )
        elif not self._current and self._state == STATE_ERROR:
            painter.setPen(QColor(theme.GOLD))
            painter.drawText(
                QRectF(width - 84.0, 0.0, 48.0, height),
                Qt.AlignRight | Qt.AlignVCenter,
                tr("重试"),
            )

        if not self._current:
            self._paint_plus(painter)
        painter.end()

    def _paint_plus(self, painter: QPainter) -> None:
        alpha = self._plus_alpha()
        if alpha <= 0.02:
            return
        rect = self._plus_rect()
        selected = self._selected()
        circle = QColor(theme.ACCENT if selected else "#FFFFFF")
        circle.setAlphaF((0.26 if selected else 0.10) * alpha)
        painter.setPen(Qt.NoPen)
        painter.setBrush(circle)
        painter.drawEllipse(rect)
        border = QColor(theme.ACCENT if selected else "#FFFFFF")
        border.setAlphaF((0.85 if selected else 0.38) * alpha)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        glyph = QColor("#FFFFFF" if selected else "#E7EBF3")
        glyph.setAlphaF(alpha)
        icons.paint_icon(
            painter,
            "minus" if selected else "plus",
            rect.adjusted(5.0, 5.0, -5.0, -5.0),
            glyph,
            1.5,
        )


class _EpisodeDrawer(QFrame):
    play_requested = Signal(int)
    preload_requested = Signal(int)
    download_requested = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Drawer")
        self.setStyleSheet(
            "QFrame#Drawer { background-color: rgba(9, 12, 17, 240);"
            " border: 1px solid rgba(255,255,255,22); border-radius: 14px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("剧集")
        self.title.setStyleSheet(
            "QLabel { color: #EDEFF3; font-size: 13px; font-weight: 600; }"
        )
        head.addWidget(self.title)
        self.hint = QLabel("")
        self.hint.setStyleSheet("QLabel { color: #7E8797; font-size: 10.5px; }")
        head.addWidget(self.hint)
        head.addStretch(1)
        self.count = QLabel("")
        self.count.setStyleSheet("QLabel { color: #7E8797; font-size: 11px; }")
        head.addWidget(self.count)
        layout.addLayout(head)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        layout.addWidget(self.scroll, 1)
        self.host = QWidget()
        self.host.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.host)
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(5)
        self._rows: dict[int, EpisodeRow] = {}
        self._current_nid = 0

    def set_source(self, name: str, episodes: list[Episode], current_nid: int) -> None:
        self.title.setText("剧集列表")
        self.count.setText(tr("{name} · 共 {n} 集", name=name, n=len(episodes)))
        for row in self._rows.values():
            self.list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows.clear()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._current_nid = current_nid
        for order, episode in enumerate(episodes):
            row = EpisodeRow(episode.nid, self._row_label(episode), self.host)
            row.set_current(episode.nid == current_nid)
            row.setToolTip(tr("点击播放；鼠标移到右侧的「+」可以预加载这一集"))
            row.activated.connect(self.play_requested)
            row.preload_toggled.connect(self.preload_requested)
            row.download_requested.connect(self.download_requested)
            self.list_layout.addWidget(row)
            self._rows[episode.nid] = row
        self.list_layout.addStretch(1)

    @staticmethod
    def _row_label(episode: Episode) -> str:
        text = " ".join((episode.label or "").split())
        return text or tr("第{ep}集", ep=episode.nid)

    def set_current(self, nid: int) -> None:
        self._current_nid = int(nid)
        for row_nid, row in self._rows.items():
            row.set_current(row_nid == self._current_nid)

    def set_preload(self, update: PreloadUpdate) -> None:
        row = self._rows.get(update.nid)
        if row is not None:
            row.set_preload(update.state, update.percent)

    def sync_preload(self, states: dict) -> None:
        for nid, row in self._rows.items():
            update = states.get(nid)
            row.set_preload(update.state, update.percent) if update else row.set_preload("idle", 0.0)

    def set_preload_hint(self, text: str) -> None:
        self.hint.setText(text)

    def scroll_to_current(self) -> None:
        row = self._rows.get(self._current_nid)
        if row is not None:
            self.scroll.ensureWidgetVisible(row)


class PlayerPage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self._media = MediaClient()
        self._detail: Optional[AnimeDetail] = None
        self._info: Optional[PlayInfo] = None
        self._sid = 1
        self._nid = 1
        self._session = None
        self._session_id = ""
        self._token = 0
        self._pending_position = 0.0
        self._duration = 0
        self._seeking = False
        self._dragging = False
        self._controls_visible = True
        self._cinema = False
        self._preloaded: dict[int, list[Segment]] = {}
        self._error_text = ""
        self._last_persist = 0.0
        self._stalled_at = 0.0
        self._last_pos = 0
        self._proxy_url = ""
        self._recovered_at = 0.0
        self._seek_target: Optional[int] = None
        self._seek_started = 0.0
        self._seek_retries = 0
        self._seek_nudge_at = 0.0

        self.canvas = VideoCanvas(self)
        self.canvas.mouse_moved.connect(self._on_activity)
        self.canvas.double_clicked.connect(self.toggle_cinema)
        self.canvas.key_pressed.connect(self._on_key)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(max(0.0, min(1.0, ctx.settings.volume / 100.0)))
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.canvas.sink)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)

        self.overlay = _Overlay(self.canvas)
        self.overlay.mouse_moved.connect(self._on_activity)
        self.overlay.double_clicked.connect(self.toggle_cinema)
        self._build_overlay()

        self.drawer = _EpisodeDrawer(self.canvas)
        self.drawer.hide()
        self.drawer.play_requested.connect(self._play_nid)
        self.drawer.preload_requested.connect(self._toggle_preload)
        self.drawer.download_requested.connect(self._download_nid)

        # 剧集预加载：排队拉取选中集的前 N% 分片，写进播放缓存
        self.preloader = PreloadManager(ctx.api, ctx.proxy.cache_root, self)
        self.preloader.update.connect(self._on_preload_update)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(CONTROL_HIDE_MS)
        self._hide_timer.timeout.connect(self._hide_controls)

        self._persist_timer = QTimer(self)
        self._persist_timer.setInterval(10000)
        self._persist_timer.timeout.connect(self._persist)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_cache_label)

        self._buffer_timer = QTimer(self)
        self._buffer_timer.setInterval(600)
        self._buffer_timer.timeout.connect(self._update_buffer_bar)

        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(1000)
        self._stall_timer.timeout.connect(self._check_stall)

    # ---------------------------------------------------------------- 结构
    def _build_overlay(self) -> None:
        layout = QVBoxLayout(self.overlay)
        layout.setContentsMargins(26, 20, 20, 18)
        layout.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.back_button = IconButton("chevron-left", 36, 20, "#FFFFFF", "#FFFFFF")
        self.back_button.setToolTip("返回详情")
        self.back_button.clicked.connect(self._go_back)
        top.addWidget(self.back_button)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet(
            "QLabel { color: #FFFFFF; font-size: 17px; font-weight: 600;"
            " background: transparent; }"
        )
        title_block.addWidget(self.title_label)
        self.subtitle_label = QLabel("")
        self.subtitle_label.setStyleSheet(
            "QLabel { color: rgba(233,238,247,168); font-size: 12px; background: transparent; }"
        )
        title_block.addWidget(self.subtitle_label)
        top.addLayout(title_block)
        top.addStretch(1)
        self.cache_label = QLabel("")
        self.cache_label.setStyleSheet(
            "QLabel { color: rgba(233,238,247,150); font-size: 11px; background: transparent; }"
        )
        top.addWidget(self.cache_label)
        self.source_chip = QLabel("")
        self.source_chip.setStyleSheet(
            "QLabel { color: #FFFFFF; font-size: 11px; background-color: rgba(255,255,255,26);"
            " border: 1px solid rgba(255,255,255,34); border-radius: 10px;"
            " padding: 4px 10px; }"
        )
        top.addWidget(self.source_chip)
        self.download_button = IconButton("download", 34, 18, "#FFFFFF", theme.ACCENT_HOVER)
        self.download_button.setToolTip("下载本集")
        self.download_button.clicked.connect(lambda: self._download_nid(self._nid))
        top.addWidget(self.download_button)
        self.list_button = IconButton("list", 34, 18, "#FFFFFF", theme.ACCENT_HOVER)
        self.list_button.setToolTip("剧集列表")
        self.list_button.clicked.connect(self._toggle_drawer)
        top.addWidget(self.list_button)
        layout.addLayout(top)
        layout.addStretch(1)

        center = QVBoxLayout()
        center.setSpacing(10)
        center.setAlignment(Qt.AlignCenter)
        self.spinner = Spinner(30, theme.ACCENT, self.overlay)
        center.addWidget(self.spinner, 0, Qt.AlignHCenter)
        self.center_label = QLabel("")
        self.center_label.setAlignment(Qt.AlignCenter)
        self.center_label.setStyleSheet(
            "QLabel { color: #E9EEF7; font-size: 13px; background: transparent; }"
        )
        center.addWidget(self.center_label)
        self.retry_button = QPushButton("重新加载")
        self.retry_button.setCursor(Qt.PointingHandCursor)
        self.retry_button.setStyleSheet(
            "QPushButton { background-color: rgba(255,255,255,20); color: #FFFFFF;"
            " border: 1px solid rgba(255,255,255,40); border-radius: 9px;"
            " padding: 7px 18px; font-size: 12px; }"
            "QPushButton:hover { background-color: rgba(255,255,255,34); }"
        )
        self.retry_button.clicked.connect(self._retry)
        center.addWidget(self.retry_button, 0, Qt.AlignHCenter)
        self.center_box = QWidget(self.overlay)
        self.center_box.setLayout(center)
        self.center_box.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.center_box.hide()
        layout.addWidget(self.center_box, 0, Qt.AlignHCenter)
        layout.addStretch(1)

        deck = QFrame(self.overlay)
        deck.setObjectName("Deck")
        deck.setStyleSheet(
            "QFrame#Deck { background-color: rgba(11, 14, 20, 208);"
            " border: 1px solid rgba(255,255,255,20); border-radius: 16px; }"
        )
        deck_layout = QVBoxLayout(deck)
        deck_layout.setContentsMargins(18, 12, 18, 10)
        deck_layout.setSpacing(6)

        self.seek = QSlider(Qt.Horizontal, deck)
        self.seek.setObjectName("SeekSlider")
        self.seek.setRange(0, 1)
        self.seek.setCursor(Qt.PointingHandCursor)
        self.seek.sliderPressed.connect(self._on_seek_press)
        self.seek.sliderMoved.connect(self._on_seek_move)
        self.seek.sliderReleased.connect(self._on_seek_release)
        seek_box = QWidget(deck)
        seek_box.setStyleSheet("background: transparent;")
        seek_layout = QVBoxLayout(seek_box)
        seek_layout.setContentsMargins(0, 0, 0, 0)
        seek_layout.setSpacing(1)
        seek_layout.addWidget(self.seek)
        self.buffer_bar = BufferBar(seek_box)
        seek_layout.addWidget(self.buffer_bar)
        deck_layout.addWidget(seek_box)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.play_button = IconButton("play", 38, 22, "#FFFFFF", theme.ACCENT_HOVER)
        self.play_button.setToolTip("播放 / 暂停（空格）")
        self.play_button.clicked.connect(self.toggle_play)
        row.addWidget(self.play_button)
        self.prev_button = IconButton("prev", 32, 17, "#C9D1DE", "#FFFFFF")
        self.prev_button.setToolTip("上一集")
        self.prev_button.clicked.connect(self._prev_episode)
        row.addWidget(self.prev_button)
        self.next_button = IconButton("next", 32, 17, "#C9D1DE", "#FFFFFF")
        self.next_button.setToolTip("下一集")
        self.next_button.clicked.connect(self._next_episode)
        row.addWidget(self.next_button)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "QLabel { color: #E7EBF2; font-size: 12px; background: transparent; }"
        )
        row.addWidget(self.time_label)
        row.addStretch(1)
        self.speed_button = QPushButton("1.0x")
        self.speed_button.setCursor(Qt.PointingHandCursor)
        self.speed_button.setStyleSheet(
            "QPushButton { background-color: transparent; color: #C9D1DE; border: none;"
            " font-size: 12px; padding: 4px 8px; }"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self.speed_button.clicked.connect(self._cycle_speed)
        row.addWidget(self.speed_button)
        self.volume_button = IconButton("volume", 30, 16, "#C9D1DE", "#FFFFFF")
        self.volume_button.clicked.connect(self._toggle_mute)
        row.addWidget(self.volume_button)
        self.volume = QSlider(Qt.Horizontal, deck)
        self.volume.setObjectName("VolumeSlider")
        self.volume.setRange(0, 100)
        self.volume.setValue(self.ctx.settings.volume)
        self.volume.setFixedWidth(88)
        self.volume.setCursor(Qt.PointingHandCursor)
        self.volume.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume)
        self.full_button = IconButton("fullscreen", 30, 16, "#C9D1DE", "#FFFFFF")
        self.full_button.setToolTip("全屏（F）")
        self.full_button.clicked.connect(self.toggle_cinema)
        row.addWidget(self.full_button)
        deck_layout.addLayout(row)

        layout.addWidget(deck)
        self.deck = deck

    # ---------------------------------------------------------------- 生命周期
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self) -> None:
        self.canvas.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.raise_()
        drawer_w = min(372, max(288, int(self.width() * 0.28)))
        top = 74 if self.height() > 460 else 8
        bottom = 118 if self.height() > 460 else 8
        self._drawer_rect = QRect(
            self.width() - drawer_w - 20,
            top,
            drawer_w,
            max(160, self.height() - top - bottom),
        )
        self.drawer.setGeometry(self._drawer_rect)
        if self.drawer.isVisible():
            self.drawer.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._layout_children()
        self.canvas.setFocus(Qt.OtherFocusReason)

    def on_show(self, **kwargs) -> None:
        self._layout_children()

    def on_hide(self) -> None:
        # 先记下进度，再把播放彻底停掉：退到主界面后不应该还有声音或后台拉流
        self._persist()
        self._save_position_now()
        self._stop_playback()

    def _stop_playback(self) -> None:
        """停播放、停定时器、释放本地代理会话，画面与进度归零。"""
        self._token += 1
        self._pending_position = 0.0
        self._preloaded.clear()
        self._proxy_url = ""
        try:
            self.preloader.cancel_all()
        except RuntimeError:
            pass
        for timer in (self._persist_timer, self._stats_timer, self._stall_timer, self._hide_timer, self._buffer_timer):
            timer.stop()
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except RuntimeError:
            pass
        try:
            self._cancel_session()
        except RuntimeError:
            pass
        self._error_text = ""
        self._hide_busy()
        self.retry_button.hide()
        self.canvas.clear()
        self._duration = 0
        self._last_pos = 0
        self._stalled_at = 0.0
        self.seek.blockSignals(True)
        self.seek.setRange(0, 1)
        self.seek.setValue(0)
        self.seek.blockSignals(False)
        self.time_label.setText("00:00 / 00:00")
        self.cache_label.setText("")
        self.buffer_bar.clear()
        self._controls_visible = True
        self.overlay.show()
        self.overlay.set_opacity(1.0)
        self.setCursor(Qt.ArrowCursor)
        if self.drawer.isVisible():
            self.drawer.hide()
            self.list_button.set_icon_name("list")

    # ---------------------------------------------------------------- 打开剧集
    def open(self, detail: AnimeDetail, sid: int, nid: int, position: float = 0.0) -> None:
        self._cancel_session()
        self._detail = detail
        self._sid = sid
        self._nid = nid
        self._pending_position = max(0.0, float(position or 0.0))
        self._preloaded.clear()
        self._error_text = ""
        self._update_header()
        source = detail.source(sid)
        if source is not None:
            self.preloader.set_context(detail.vod_id, sid)
            self.preloader.set_playing(nid)
            self.drawer.set_source(source.name, source.episodes, nid)
            self.drawer.set_current(nid)
            self.drawer.sync_preload(self.preloader.states())
            self._sync_preload_hint()
        self._play_current(autoplay=True)
        self._persist_timer.start()
        self._stats_timer.start()
        self._stall_timer.start()
        self._show_controls()

    def _update_header(self) -> None:
        detail = self._detail
        if detail is None:
            return
        source = detail.source(self._sid)
        episode = source.episode(self._nid) if source is not None else None
        self.title_label.setText(elide(detail.name, 520))
        subtitle = []
        if episode is not None:
            subtitle.append(episode.label)
        if source is not None:
            subtitle.append(source.name)
        subtitle.append(tr("本地代理 · 多线程预载"))
        self.subtitle_label.setText(" · ".join(subtitle))
        self.source_chip.setText(source.name if source is not None else tr("线路"))

    def _play_current(self, autoplay: bool = True) -> None:
        detail = self._detail
        if detail is None:
            return
        self._token += 1
        token = self._token
        self.drawer.set_current(self._nid)
        self.preloader.set_playing(self._nid)
        self.drawer.sync_preload(self.preloader.states())
        self._sync_preload_hint()
        self._update_header()
        self._show_busy("正在解析播放地址…")
        self._preloaded.pop(self._nid, None)

        vod_id = detail.vod_id
        sid = self._sid
        nid = self._nid
        referer = "%s/vod/%s/" % (self.ctx.api.base, vod_id)

        def work():
            info = self.ctx.api.play_info(vod_id, sid, nid)
            playlist = resolve_playlist(self._media, info.url, referer=referer)
            return info, playlist

        def done(result) -> None:
            if token != self._token or self._detail is None:
                return
            info, playlist = result
            self._info = info
            self._start_session(playlist.segments, referer, autoplay)
            self._preload_next()

        def failed(message: str) -> None:
            if token != self._token:
                return
            self._show_error(tr("解析播放地址失败：{error}", error=message))

        self.ctx.tasks.run(work, on_done=done, on_error=failed)

    def _start_session(self, segments: list[Segment], referer: str, autoplay: bool) -> None:
        if not segments:
            self._show_error("播放列表为空")
            return
        self._cancel_session()
        workers = max(4, self.ctx.settings.buffer_workers)
        try:
            session, url = self.ctx.proxy.open(segments, referer=referer, workers=workers)
        except Exception as exc:  # noqa: BLE001
            self._show_error(tr("启动本地代理失败：{error}", error=exc))
            return
        self._session = session
        self._session_id = session.id
        self._proxy_url = url
        self._buffer_timer.start()
        QTimer.singleShot(120, self._update_buffer_bar)
        self.player.setSource(QUrl(url))
        if autoplay:
            self.player.play()
        self._show_busy("正在缓冲…")

    def _cancel_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                self.ctx.proxy.close(session.id)
            except Exception:  # noqa: BLE001
                pass

    def _retry(self) -> None:
        self._error_text = ""
        self._play_current(autoplay=True)

    # ---------------------------------------------------------------- 播放控制
    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()
        self._show_controls()

    def _toggle_mute(self) -> None:
        muted = not self.audio.isMuted()
        self.audio.setMuted(muted)
        self.volume_button.set_icon_name("muted" if muted else "volume")
        self._show_controls()

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(max(0.0, min(1.0, value / 100.0)))
        if value > 0 and self.audio.isMuted():
            self.audio.setMuted(False)
            self.volume_button.set_icon_name("volume")
        self.ctx.settings.volume = int(value)

    def _cycle_speed(self) -> None:
        steps = [0.75, 1.0, 1.25, 1.5, 2.0]
        try:
            index = steps.index(round(self.player.playbackRate() * 100) / 100)
        except ValueError:
            index = 1
        rate = steps[(index + 1) % len(steps)]
        self.player.setPlaybackRate(rate)
        self.ctx.settings.speed = float(rate)
        self.speed_button.setText("%.2fx" % rate if rate != round(rate, 1) else "%.1fx" % rate)
        self._show_controls()

    def _apply_speed(self) -> None:
        rate = max(0.5, min(3.0, float(self.ctx.settings.speed or 1.0)))
        self.player.setPlaybackRate(rate)
        self.speed_button.setText("%.2fx" % rate if rate != round(rate, 1) else "%.1fx" % rate)

    def _seek_to(self, milliseconds: int) -> None:
        # 先把目标分片提到队首，再让播放器跳；不然预取任务排在前面，跳过去要等很久
        target = max(0, int(milliseconds))
        if self._duration > 0:
            target = min(target, max(0, self._duration - 800))
        self._warm_cache(target)
        self._seek_target = target
        self._seek_started = time.time()
        self._seek_retries = 0
        self._seek_nudge_at = 0.0
        self._stalled_at = 0.0
        status = self.player.mediaStatus()
        if status in (
            QMediaPlayer.NoMedia,
            QMediaPlayer.LoadingMedia,
            QMediaPlayer.StalledMedia,
            QMediaPlayer.InvalidMedia,
        ):
            # 还没就绪时 setPosition 会被丢掉，等载入后再跳，避免「跳回开头」
            self._pending_position = target / 1000.0 + 0.001
            return
        self.player.setPosition(target)

    def _nudge_seek(self) -> None:
        """再催一次 seek：播放器偶尔会把跳转丢掉，重新设一次位置。"""
        target = self._seek_target
        if target is None:
            return
        self._seek_nudge_at = time.time()
        self._seek_retries += 1
        self._warm_cache(target)
        self.player.setPosition(target)

    def _seek_tolerance(self) -> int:
        session = self._session
        if session is not None and session.segments:
            longest = max((seg.duration or 0.0) for seg in session.segments)
            return int(max(3000, longest * 1000 * 1.6))
        return 4000

    def _warm_cache(self, milliseconds: int) -> None:
        session = self._session
        if session is None or not session.segments:
            return
        target = max(0, milliseconds) / 1000.0
        playhead = 0
        total = 0.0
        for index, segment in enumerate(session.segments):
            if total + (segment.duration or 0.0) >= target:
                playhead = index
                break
            total += segment.duration or 0.0
            playhead = index
        try:
            session.bump(playhead, ahead=max(8, self.ctx.settings.buffer_workers // 2))
        except Exception:  # noqa: BLE001 - 预热失败不影响正常播放
            pass

    def seek_by(self, delta_ms: int) -> None:
        current = self.player.position()
        self._seek_to(max(0, current + delta_ms))
        self._show_controls()

    def _on_seek_press(self) -> None:
        self._dragging = True

    def _on_seek_move(self, value: int) -> None:
        self._dragging = True
        self.time_label.setText(
            "%s / %s" % (fmt_duration(value / 1000.0), fmt_duration(self._duration / 1000.0))
        )

    def _on_seek_release(self) -> None:
        self._dragging = False
        value = self.seek.value()
        self._seek_to(value)
        self._show_controls()

    # ---------------------------------------------------------------- 剧集切换
    def _play_nid(self, nid: int) -> None:
        if self._detail is None:
            return
        source = self._detail.source(self._sid)
        if source is None or source.episode(nid) is None:
            return
        self._persist()
        self._nid = nid
        self._pending_position = 0.0
        self.drawer.set_current(nid)
        self._play_current(autoplay=True)
        self.drawer.hide()
        self.list_button.set_icon_name("list")

    def _next_episode(self) -> None:
        if self._detail is None:
            return
        source = self._detail.source(self._sid)
        if source is None:
            return
        nids = [episode.nid for episode in source.episodes]
        if self._nid in nids:
            index = nids.index(self._nid)
            if index + 1 < len(nids):
                self._play_nid(nids[index + 1])

    def _prev_episode(self) -> None:
        if self._detail is None:
            return
        source = self._detail.source(self._sid)
        if source is None:
            return
        nids = [episode.nid for episode in source.episodes]
        if self._nid in nids:
            index = nids.index(self._nid)
            if index > 0:
                self._play_nid(nids[index - 1])

    def _download_nid(self, nid: int) -> None:
        detail = self._detail
        if detail is None:
            return
        source = detail.source(self._sid)
        if source is None:
            return
        episode = source.episode(nid)
        if episode is None:
            return
        created = self.ctx.downloads.enqueue(
            detail, source, [episode], self.ctx.download_root()
        )
        if created:
            self.ctx.notify(tr("已加入下载队列：{episode}", episode=episode.label), "download", theme.GREEN)
        else:
            self.ctx.notify("已在下载队列中", "info", theme.TEXT_DIM)

    def _toggle_preload(self, nid: int) -> None:
        """剧集列表右侧的「+」：加入/取消预加载（失败状态点一下就重来）。"""
        detail = self._detail
        if detail is None:
            return
        source = detail.source(self._sid)
        if source is None:
            return
        episode = source.episode(nid)
        if episode is None:
            return
        order = next((index for index, item in enumerate(source.episodes) if item.nid == nid), nid)
        state = self.preloader.states().get(nid)
        if state is not None and state.state == STATE_ERROR:
            # 失败的那一集：先摘掉再重新排队
            self.preloader.toggle(
                nid,
                episode.label,
                order=order,
                max_count=self.ctx.settings.preload_count,
                percent=self.ctx.settings.preload_percent,
            )
        result = self.preloader.toggle(
            nid,
            episode.label,
            order=order,
            max_count=self.ctx.settings.preload_count,
            percent=self.ctx.settings.preload_percent,
        )
        if result == "refused":
            self.ctx.notify(
                tr("最多同时预加载 {n} 集，先在列表里取消一集", n=self.ctx.settings.preload_count),
                "info",
                theme.TEXT_DIM,
            )
        elif result == "busy":
            self.ctx.notify(tr("正在播放的这一集不用预加载"), "info", theme.TEXT_DIM)
        elif result == "unavailable":
            self.ctx.notify(tr("播放缓存不可用，暂时无法预加载"), "alert", theme.GOLD)
        self.drawer.sync_preload(self.preloader.states())
        self._sync_preload_hint()

    def _on_preload_update(self, update: PreloadUpdate) -> None:
        self.drawer.set_preload(update)
        if update.state == STATE_DONE:
            episode = None
            source = self._detail.source(self._sid) if self._detail is not None else None
            if source is not None:
                episode = source.episode(update.nid)
            if episode is not None:
                self.ctx.notify(
                    tr("已预加载 {episode} 的开头片段", episode=episode.label),
                    "check",
                    theme.GREEN,
                )
        elif update.state == STATE_ERROR:
            self.ctx.notify(tr("预加载失败，点右侧按钮可以重试"), "alert", theme.GOLD)

    def _sync_preload_hint(self) -> None:
        self.drawer.set_preload_hint(
            tr(
                "· 预加载 {used}/{max}",
                used=self.preloader.count(),
                max=self.ctx.settings.preload_count,
            )
        )

    def _preload_next(self) -> None:
        detail = self._detail
        if detail is None:
            return
        source = detail.source(self._sid)
        if source is None:
            return
        nids = [episode.nid for episode in source.episodes]
        if self._nid not in nids:
            return
        index = nids.index(self._nid)
        if index + 1 >= len(nids):
            return
        next_nid = nids[index + 1]
        if next_nid in self._preloaded:
            return
        vod_id = detail.vod_id
        sid = self._sid
        referer = "%s/vod/%s/" % (self.ctx.api.base, vod_id)

        def work():
            info = self.ctx.api.play_info(vod_id, sid, next_nid)
            return resolve_playlist(self._media, info.url, referer=referer)

        def done(playlist) -> None:
            if not self.isVisible():
                return
            if playlist.segments:
                self._preloaded[next_nid] = playlist.segments

        self.ctx.tasks.run(work, on_done=done, on_error=None)

    # ---------------------------------------------------------------- 播放器信号
    def _on_position(self, position: int) -> None:
        if self._pending_position > 0 and position <= 0:
            return
        if self._pending_position > 0 and position > 0:
            target = int(self._pending_position * 1000)
            self._pending_position = 0.0
            if target > 1500:
                self.player.setPosition(target)
                return
        if self._seek_target is not None:
            elapsed = time.time() - self._seek_started
            if (
                position < 1500
                and self._seek_target > 5000
                and elapsed > 1.0
                and time.time() - self._seek_nudge_at > 1.5
                and self._seek_retries < 3
            ):
                # 播放器被拉回开头（分片抖动或重载），重新跳一次
                self._nudge_seek()
                return
            if abs(position - self._seek_target) <= self._seek_tolerance():
                self._seek_target = None
        if self._dragging:
            return
        self.seek.blockSignals(True)
        self.seek.setValue(position)
        self.seek.blockSignals(False)
        self.time_label.setText(
            "%s / %s" % (fmt_duration(position / 1000.0), fmt_duration(self._duration / 1000.0))
        )
        if position > 0:
            self._hide_busy()
        if position != self._last_pos:
            self._last_pos = position
            self._stalled_at = 0.0

    def _on_duration(self, duration: int) -> None:
        self._duration = max(0, duration)
        self.seek.setRange(0, max(1, self._duration))
        self.time_label.setText(
            "%s / %s" % (fmt_duration(self.player.position() / 1000.0), fmt_duration(self._duration / 1000.0))
        )
        self._apply_speed()
        self._update_buffer_bar()
        if self._pending_position > 0:
            target = int(self._pending_position * 1000)
            self._pending_position = 0.0
            if target > 1500:
                self.player.setPosition(min(target, max(0, self._duration - 1000)))

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._hide_busy()
            if self._seek_target is not None and self.player.position() < 1500 and self._seek_target > 5000:
                QTimer.singleShot(150, self._nudge_seek)
        elif status == QMediaPlayer.BufferingMedia:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self._show_busy("缓冲中…")
        elif status == QMediaPlayer.StalledMedia:
            self._show_busy("网络较慢，正在重试…")
        elif status == QMediaPlayer.EndOfMedia:
            if self._duration <= 0 or self.player.position() >= self._duration - 1600:
                self._on_finished()
        elif status == QMediaPlayer.InvalidMedia:
            self._show_error("播放器无法解析该视频流")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_button.set_icon_name("pause" if playing else "play")
        if playing:
            self._restart_hide_timer()
        else:
            self._show_controls()

    def _on_error(self, error, message: str) -> None:
        if error == QMediaPlayer.NoError:
            return
        self._show_error(tr("播放出错：{error}", error=message or error))

    def _check_stall(self) -> None:
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self._stalled_at = 0.0
            return
        position = self.player.position()
        if self._seek_target is not None:
            # 拖动进度条期间不按普通卡顿处理：先催播放器跳到位，再考虑重连
            waited = time.time() - self._seek_started
            if waited > 5.0 and time.time() - self._seek_nudge_at > 3.0:
                if self._seek_retries < 3:
                    self._show_busy("正在跳转…")
                    self._nudge_seek()
                else:
                    self._recover_playback(self._seek_target / 1000.0)
            return
        if position == self._last_pos:
            self._stalled_at += 1.0
            if self._stalled_at >= 4.0:
                self._show_busy("缓冲中…")
            if self._stalled_at >= 9.0:
                self._recover_playback()
        else:
            self._stalled_at = 0.0

    def _recover_playback(self, seconds: Optional[float] = None) -> None:
        """播放器卡死时重新挂一次本地清单，并跳回原位置（或指定的位置）。"""
        now = time.time()
        if not self._proxy_url or now - self._recovered_at < 15.0:
            return
        self._recovered_at = now
        self._stalled_at = 0.0
        target = max(1.2, seconds if seconds is not None else self.player.position() / 1000.0)
        self._seek_started = now
        self._show_busy("正在重新连接…")
        self._pending_position = target + 0.001
        self._warm_cache(int(target * 1000))
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except RuntimeError:
            pass

        def reload() -> None:
            if not self._proxy_url or not self.isVisible():
                return
            self.player.setSource(QUrl(self._proxy_url))
            self.player.play()

        QTimer.singleShot(80, reload)

    def _on_finished(self) -> None:
        self._persist()
        if not self.ctx.settings.auto_next:
            self._show_controls()
            return
        source = self._detail.source(self._sid) if self._detail else None
        if source is None:
            return
        nids = [episode.nid for episode in source.episodes]
        if self._nid in nids and nids.index(self._nid) + 1 < len(nids):
            next_nid = nids[nids.index(self._nid) + 1]
            cached = self._preloaded.pop(next_nid, None)
            self._nid = next_nid
            self._pending_position = 0.0
            self.drawer.set_current(next_nid)
            self.preloader.set_playing(next_nid)
            self.drawer.sync_preload(self.preloader.states())
            self._sync_preload_hint()
            self._update_header()
            if cached:
                self._nid = next_nid
                self._start_session(
                    cached,
                    "%s/vod/%s/" % (self.ctx.api.base, self._detail.vod_id),
                    autoplay=True,
                )
                self._preload_next()
            else:
                self._play_current(autoplay=True)
            self.ctx.notify("自动播放下一集", "play", theme.ACCENT)
        else:
            self._show_busy("本线路已播放完毕")

    # ---------------------------------------------------------------- 覆盖层状态
    def _show_busy(self, text: str) -> None:
        self.spinner.start()
        self.spinner.show()
        self.center_label.setText(tr(text))
        self.center_label.show()
        self.retry_button.hide()
        self.center_box.show()
        self._show_controls()

    def _hide_busy(self) -> None:
        self.spinner.stop()
        self.spinner.hide()
        self.center_label.hide()
        self.center_box.hide()

    def _show_error(self, text: str) -> None:
        self._error_text = tr(text)
        self.spinner.stop()
        self.spinner.hide()
        self.center_label.setText(self._error_text)
        self.center_label.show()
        self.retry_button.show()
        self.center_box.show()
        self._show_controls()
        self._hide_timer.stop()

    def _on_activity(self) -> None:
        self._show_controls()

    def _show_controls(self) -> None:
        if not self._controls_visible:
            self._controls_visible = True
            self.overlay.show()
            self.overlay.set_opacity(1.0)
            self.setCursor(Qt.ArrowCursor)
        self.overlay.show()
        self._restart_hide_timer()

    def _restart_hide_timer(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState and not self.drawer.isVisible():
            self._hide_timer.start()
        else:
            self._hide_timer.stop()

    def _hide_controls(self) -> None:
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            return
        if self.drawer.isVisible():
            return
        self._controls_visible = False
        self.overlay.set_opacity(0.0)
        QTimer.singleShot(200, self._finish_hide)

    def _finish_hide(self) -> None:
        if not self._controls_visible:
            self.setCursor(Qt.BlankCursor)

    def _toggle_drawer(self) -> None:
        if self.drawer.isVisible():
            self._animate_drawer(False)
            return
        self.drawer.setVisible(True)
        self.drawer.raise_()
        self.drawer.scroll_to_current()
        self.list_button.set_icon_name("chevron-right")
        self._hide_timer.stop()
        self._show_controls()
        self._animate_drawer(True)

    def _animate_drawer(self, show: bool) -> None:
        """剧集抽屉从右侧滑入/滑出（只动位置，不重排里面的列表）。"""
        rect = getattr(self, "_drawer_rect", QRect())
        if rect.isEmpty():
            self.drawer.setVisible(show)
            return
        self.drawer.setGeometry(rect)
        duration = theme.motion(200)
        end = QPoint(rect.x(), rect.y())
        start = QPoint(rect.x() + (30 if show else 0), rect.y())
        finish = QPoint(rect.x() + (0 if show else 30), rect.y())
        if duration <= 0:
            self.drawer.move(finish if not show else end)
            if not show:
                self._finish_hide_drawer()
            return
        self.drawer.move(start)
        anim = QPropertyAnimation(self.drawer, b"pos", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end if show else finish)
        anim.setEasingCurve(QEasingCurve.OutCubic if show else QEasingCurve.InCubic)
        if not show:
            anim.finished.connect(self._finish_hide_drawer)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._drawer_anim = anim

    def _finish_hide_drawer(self) -> None:
        self.drawer.hide()
        rect = getattr(self, "_drawer_rect", QRect())
        if not rect.isEmpty():
            self.drawer.move(rect.x(), rect.y())
        self.list_button.set_icon_name("list")
        self._restart_hide_timer()

    # ---------------------------------------------------------------- 缓存与进度
    def _update_buffer_bar(self) -> None:
        """进度条下面的白色缓冲条：跟随本地已经拉好的分片。"""
        session = self._session
        if session is None:
            self.buffer_bar.clear()
            return
        try:
            ranges = session.buffer_ranges()
        except Exception:  # noqa: BLE001
            return
        duration = session.duration or (self._duration / 1000.0)
        self.buffer_bar.set_buffer(ranges, duration)

    def _update_cache_label(self) -> None:
        session = self._session
        if session is None:
            self.cache_label.setText("")
            return
        try:
            cached, total = session.stats()
        except Exception:  # noqa: BLE001
            return
        if total <= 0:
            self.cache_label.setText("")
            return
        percent = int(cached * 100 / total)
        self.cache_label.setText(tr("已预载 {percent}%（{cached}/{total} 段）", percent=percent, cached=cached, total=total))

    def _persist(self) -> None:
        detail = self._detail
        if detail is None:
            return
        source = detail.source(self._sid)
        episode = source.episode(self._nid) if source is not None else None
        position = self.player.position() / 1000.0
        duration = self._duration / 1000.0
        if position <= 1.0:
            return
        self.ctx.store.touch_history(
            vod_id=detail.vod_id,
            name=detail.name,
            cover=detail.cover,
            sid=self._sid,
            source_name=source.name if source is not None else "",
            nid=self._nid,
            episode=episode.label if episode is not None else "",
            position=position,
            duration=duration,
            total_episodes=len(source.episodes) if source is not None else 0,
        )
        now = time.time()
        if now - self._last_persist > 30.0:
            self._last_persist = now
            self.ctx.save()

    def _save_position_now(self) -> None:
        self._persist()
        self.ctx.save()

    # ---------------------------------------------------------------- 键盘
    def _on_key(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self.toggle_play()
            event.accept()
        elif key == Qt.Key_Left:
            self.seek_by(-SEEK_STEP)
            event.accept()
        elif key == Qt.Key_Right:
            self.seek_by(SEEK_STEP)
            event.accept()
        elif key == Qt.Key_Up:
            self.volume.setValue(min(100, self.volume.value() + 5))
            event.accept()
        elif key == Qt.Key_Down:
            self.volume.setValue(max(0, self.volume.value() - 5))
            event.accept()
        elif key == Qt.Key_F:
            self.toggle_cinema()
            event.accept()
        elif key == Qt.Key_Escape:
            if self._cinema:
                self.toggle_cinema()
            else:
                self._go_back()
            event.accept()
        elif key == Qt.Key_N:
            self._next_episode()
            event.accept()
        elif key == Qt.Key_P:
            self._prev_episode()
            event.accept()

    # ---------------------------------------------------------------- 全屏
    def toggle_cinema(self) -> None:
        self._cinema = not self._cinema
        self.full_button.set_icon_name("windowed" if self._cinema else "fullscreen")
        self.shell.set_cinema(self._cinema)
        self._layout_children()
        if self._cinema:
            self._hide_timer.stop()
        else:
            self._show_controls()

    def set_cinema_state(self, cinema: bool) -> None:
        self._cinema = cinema
        self.full_button.set_icon_name("windowed" if cinema else "fullscreen")

    def _go_back(self) -> None:
        self._persist()
        if self._cinema:
            self.toggle_cinema()
        self.shell.leave_player()
