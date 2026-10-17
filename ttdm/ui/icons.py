"""线性图标：全部用 QPainterPath 绘制，不依赖字体图标、不使用 emoji。

统一 24x24 坐标系、圆头笔帽、1.6px 描边，按 2 倍像素密度渲染保证高分屏清晰。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_GRID = 24.0


def _poly(path: QPainterPath, points: list[tuple[float, float]], close: bool = False) -> None:
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()


def _chevron(direction: str) -> Callable[[QPainter], None]:
    def draw(painter: QPainter) -> None:
        path = QPainterPath()
        if direction == "left":
            _poly(path, [(15.2, 4.8), (8.0, 12.0), (15.2, 19.2)])
        elif direction == "right":
            _poly(path, [(8.8, 4.8), (16.0, 12.0), (8.8, 19.2)])
        elif direction == "up":
            _poly(path, [(4.8, 15.2), (12.0, 8.0), (19.2, 15.2)])
        else:
            _poly(path, [(4.8, 8.8), (12.0, 16.0), (19.2, 8.8)])
        painter.drawPath(path)

    return draw


def _arc_arrow(painter: QPainter) -> None:
    path = QPainterPath()
    path.arcMoveTo(QRectF(4.0, 4.0, 16.0, 16.0), 45.0)
    path.arcTo(QRectF(4.0, 4.0, 16.0, 16.0), 45.0, 285.0)
    painter.drawPath(path)
    head = QPainterPath()
    _poly(head, [(16.2, 2.6), (20.6, 5.6), (17.0, 9.2)])
    painter.drawPath(head)


def _search(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(10.6, 10.6), 6.2, 6.2)
    path = QPainterPath()
    _poly(path, [(15.4, 15.4), (20.6, 20.6)])
    painter.drawPath(path)


def _play(painter: QPainter, filled: bool = True) -> None:
    path = QPainterPath()
    _poly(path, [(8.2, 4.8), (19.2, 12.0), (8.2, 19.2)], close=True)
    if filled:
        painter.fillPath(path, painter.pen().color())
    else:
        painter.drawPath(path)


def _pause(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(7.6, 5.0, 3.4, 14.0), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(13.0, 5.0, 3.4, 14.0), 1.4, 1.4)


def _next(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(6.4, 5.2), (15.6, 12.0), (6.4, 18.8)], close=True)
    painter.fillPath(path, painter.pen().color())
    painter.drawLine(QPointF(18.4, 5.2), QPointF(18.4, 18.8))


def _prev(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(17.6, 5.2), (8.4, 12.0), (17.6, 18.8)], close=True)
    painter.fillPath(path, painter.pen().color())
    painter.drawLine(QPointF(5.6, 5.2), QPointF(5.6, 18.8))


def _download(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(12.0, 3.6), (12.0, 15.4)])
    _poly(path, [(6.8, 10.2), (12.0, 15.4), (17.2, 10.2)])
    painter.drawPath(path)
    painter.drawLine(QPointF(4.4, 20.4), QPointF(19.6, 20.4))


def _upload(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(12.0, 20.4), (12.0, 8.6)])
    _poly(path, [(6.8, 13.8), (12.0, 8.6), (17.2, 13.8)])
    painter.drawPath(path)
    painter.drawLine(QPointF(4.4, 3.6), QPointF(19.6, 3.6))


def _home(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(3.4, 10.8), (12.0, 3.6), (20.6, 10.8)])
    painter.drawPath(path)
    body = QPainterPath()
    _poly(body, [(6.0, 9.8), (6.0, 20.4), (18.0, 20.4), (18.0, 9.8)])
    painter.drawPath(body)
    painter.drawLine(QPointF(10.2, 20.4), QPointF(10.2, 14.6))
    painter.drawLine(QPointF(13.8, 20.4), QPointF(13.8, 14.6))
    painter.drawLine(QPointF(10.2, 14.6), QPointF(13.8, 14.6))


def _film(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(3.4, 5.4, 17.2, 13.2), 2.0, 2.0)
    for x in (6.6, 17.4):
        for y in (8.4, 12.0, 15.6):
            painter.drawPoint(QPointF(x, y))
    painter.drawLine(QPointF(9.4, 5.4), QPointF(9.4, 18.6))
    painter.drawLine(QPointF(14.6, 5.4), QPointF(14.6, 18.6))


def _clock(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(12.0, 12.0), 8.0, 8.0)
    path = QPainterPath()
    _poly(path, [(12.0, 7.4), (12.0, 12.4), (15.6, 14.4)])
    painter.drawPath(path)


def _setting(painter: QPainter) -> None:
    for index, y in enumerate((7.4, 12.0, 16.6)):
        painter.drawLine(QPointF(4.2, y), QPointF(19.8, y))
        knobs = (9.0, 15.2, 7.6)
        painter.setBrush(painter.pen().color())
        painter.drawEllipse(QPointF(knobs[index], y), 2.0, 2.0)
        painter.setBrush(Qt.NoBrush)


def _volume(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(4.6, 9.4), (8.6, 9.4), (13.2, 5.4), (13.2, 18.6), (8.6, 14.6), (4.6, 14.6)], close=True)
    painter.drawPath(path)
    wave = QPainterPath()
    wave.moveTo(16.2, 8.8)
    wave.quadTo(19.4, 12.0, 16.2, 15.2)
    painter.drawPath(wave)
    wave2 = QPainterPath()
    wave2.moveTo(18.4, 5.8)
    wave2.quadTo(23.0, 12.0, 18.4, 18.2)
    painter.drawPath(wave2)


def _muted(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(4.6, 9.4), (8.6, 9.4), (13.2, 5.4), (13.2, 18.6), (8.6, 14.6), (4.6, 14.6)], close=True)
    painter.drawPath(path)
    painter.drawLine(QPointF(16.4, 8.8), QPointF(21.4, 15.2))
    painter.drawLine(QPointF(21.4, 8.8), QPointF(16.4, 15.2))


def _fullscreen(painter: QPainter) -> None:
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = 4.0 if sx > 0 else 20.0
        y = 4.0 if sy > 0 else 20.0
        painter.drawLine(QPointF(x, y), QPointF(x + sx * 5.4, y))
        painter.drawLine(QPointF(x, y), QPointF(x, y + sy * 5.4))


def _windowed(painter: QPainter) -> None:
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = 8.6 if sx > 0 else 15.4
        y = 8.6 if sy > 0 else 15.4
        painter.drawLine(QPointF(x, y), QPointF(x + sx * 5.0, y))
        painter.drawLine(QPointF(x, y), QPointF(x, y + sy * 5.0))


def _close(painter: QPainter) -> None:
    painter.drawLine(QPointF(6.4, 6.4), QPointF(17.6, 17.6))
    painter.drawLine(QPointF(17.6, 6.4), QPointF(6.4, 17.6))


def _minimise(painter: QPainter) -> None:
    painter.drawLine(QPointF(5.6, 12.0), QPointF(18.4, 12.0))


def _maximise(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(5.4, 5.4, 13.2, 13.2), 1.8, 1.8)


def _restore(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(4.6, 7.6, 11.8, 11.8), 1.8, 1.8)
    path = QPainterPath()
    _poly(path, [(8.4, 7.6), (8.4, 4.6), (19.4, 4.6), (19.4, 15.6), (16.4, 15.6)])
    painter.drawPath(path)


def _folder(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(3.4, 7.2), (3.4, 19.4), (20.6, 19.4), (20.6, 8.4), (11.6, 8.4), (9.8, 6.2), (3.4, 6.2)], close=True)
    painter.drawPath(path)


def _check(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(4.8, 12.6), (9.8, 17.4), (19.2, 6.8)])
    painter.drawPath(path)


def _plus(painter: QPainter) -> None:
    painter.drawLine(QPointF(12.0, 5.4), QPointF(12.0, 18.6))
    painter.drawLine(QPointF(5.4, 12.0), QPointF(18.6, 12.0))


def _minus(painter: QPainter) -> None:
    painter.drawLine(QPointF(5.4, 12.0), QPointF(18.6, 12.0))


def _alert(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(12.0, 12.0), 8.4, 8.4)
    painter.drawLine(QPointF(12.0, 7.4), QPointF(12.0, 13.0))
    painter.drawPoint(QPointF(12.0, 16.6))


def _info(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(12.0, 12.0), 8.4, 8.4)
    painter.drawLine(QPointF(12.0, 11.0), QPointF(12.0, 16.6))
    painter.drawPoint(QPointF(12.0, 7.8))


def _list(painter: QPainter) -> None:
    for y in (6.6, 12.0, 17.4):
        painter.drawPoint(QPointF(5.4, y))
        painter.drawLine(QPointF(9.0, y), QPointF(19.4, y))


def _trash(painter: QPainter) -> None:
    painter.drawLine(QPointF(4.8, 6.8), QPointF(19.2, 6.8))
    path = QPainterPath()
    _poly(path, [(6.8, 6.8), (7.8, 20.0), (16.2, 20.0), (17.2, 6.8)])
    painter.drawPath(path)
    painter.drawLine(QPointF(9.6, 4.2), QPointF(14.4, 4.2))


def _external(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(13.6, 4.4), (19.6, 4.4), (19.6, 10.4)])
    painter.drawPath(path)
    painter.drawLine(QPointF(19.6, 4.4), QPointF(11.2, 12.8))
    corner = QPainterPath()
    _poly(corner, [(17.2, 14.2), (17.2, 19.6), (4.4, 19.6), (4.4, 6.8), (9.8, 6.8)])
    painter.drawPath(corner)


def _grid(painter: QPainter) -> None:
    for row in range(2):
        for col in range(2):
            painter.drawRoundedRect(QRectF(4.2 + col * 8.4, 4.2 + row * 8.4, 7.2, 7.2), 1.6, 1.6)


def _rows(painter: QPainter) -> None:
    for y in (6.4, 12.0, 17.6):
        painter.drawLine(QPointF(4.2, y), QPointF(19.8, y))


def _speed(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(4.4, 16.8)
    path.quadTo(8.4, 4.4, 19.6, 4.4)
    path.quadTo(16.4, 16.8, 4.4, 16.8)
    painter.drawPath(path)
    painter.drawEllipse(QPointF(12.0, 10.6), 1.8, 1.8)


def _sparkle(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(path, [(12.0, 3.4), (13.8, 10.2), (20.6, 12.0), (13.8, 13.8), (12.0, 20.6), (10.2, 13.8), (3.4, 12.0), (10.2, 10.2)], close=True)
    painter.drawPath(path)


def _star(painter: QPainter) -> None:
    path = QPainterPath()
    _poly(
        path,
        [
            (12.0, 3.6),
            (14.3, 8.9),
            (20.0, 9.4),
            (15.7, 13.2),
            (16.9, 18.8),
            (12.0, 15.9),
            (7.1, 18.8),
            (8.3, 13.2),
            (4.0, 9.4),
            (9.7, 8.9),
        ],
        close=True,
    )
    painter.drawPath(path)


def _flame(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(12.0, 20.4)
    path.cubicTo(7.6, 20.4, 5.4, 17.4, 5.4, 13.9)
    path.cubicTo(5.4, 10.6, 7.8, 8.6, 9.3, 6.1)
    path.cubicTo(9.9, 9.4, 11.1, 9.9, 11.7, 10.7)
    path.cubicTo(12.5, 7.5, 13.5, 5.1, 15.7, 3.6)
    path.cubicTo(15.3, 7.5, 18.6, 9.7, 18.6, 13.9)
    path.cubicTo(18.6, 17.4, 16.4, 20.4, 12.0, 20.4)
    painter.drawPath(path)


def _globe(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(12.0, 12.0), 8.2, 8.2)
    painter.drawEllipse(QRectF(8.6, 3.8, 6.8, 16.4))
    painter.drawLine(QPointF(3.9, 12.0), QPointF(20.1, 12.0))


def _moon(painter: QPainter) -> None:
    path = QPainterPath()
    path.arcMoveTo(QRectF(3.8, 3.8, 16.4, 16.4), 66.0)
    path.arcTo(QRectF(3.8, 3.8, 16.4, 16.4), 66.0, 240.0)
    path.arcTo(QRectF(7.8, 1.4, 15.4, 15.4), 300.0, -220.0)
    path.closeSubpath()
    painter.drawPath(path)


_ICONS: dict[str, Callable[[QPainter], None]] = {
    "home": _home,
    "search": _search,
    "play": lambda p: _play(p, True),
    "pause": _pause,
    "next": _next,
    "prev": _prev,
    "download": _download,
    "upload": _upload,
    "film": _film,
    "clock": _clock,
    "setting": _setting,
    "volume": _volume,
    "muted": _muted,
    "fullscreen": _fullscreen,
    "windowed": _windowed,
    "close": _close,
    "minimise": _minimise,
    "maximise": _maximise,
    "restore": _restore,
    "folder": _folder,
    "check": _check,
    "plus": _plus,
    "minus": _minus,
    "alert": _alert,
    "info": _info,
    "list": _list,
    "trash": _trash,
    "external": _external,
    "grid": _grid,
    "rows": _rows,
    "refresh": _arc_arrow,
    "speed": _speed,
    "sparkle": _sparkle,
    "star": _star,
    "flame": _flame,
    "globe": _globe,
    "moon": _moon,
    "chevron-left": _chevron("left"),
    "chevron-right": _chevron("right"),
    "chevron-down": _chevron("down"),
    "chevron-up": _chevron("up"),
}

ICON_NAMES = tuple(_ICONS)


def paint_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.6) -> None:
    """在任意 rect 内绘制图标，保持正方形与居中。"""
    draw = _ICONS.get(name)
    if draw is None:
        draw = _ICONS["info"]
    side = min(rect.width(), rect.height())
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.center())
    painter.scale(side / _GRID, side / _GRID)
    painter.translate(-_GRID / 2.0, -_GRID / 2.0)
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    draw(painter)
    painter.restore()


@lru_cache(maxsize=256)
def icon_pixmap(name: str, size: int = 20, color: str = "#EDEFF3", width: float = 1.6, dpr: float = 2.0) -> QPixmap:
    scale = max(1.0, float(dpr))
    pixmap = QPixmap(int(size * scale), int(size * scale))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    paint_icon(painter, name, QRectF(0, 0, size * scale, size * scale), QColor(color), width * scale)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


@lru_cache(maxsize=256)
def icon(name: str, size: int = 20, color: str = "#EDEFF3", width: float = 1.6) -> QIcon:
    return QIcon(icon_pixmap(name, size, color, width))
