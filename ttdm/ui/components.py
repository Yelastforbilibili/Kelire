"""可复用界面组件：封面、海报卡、横滑行、区块标题、空状态、提示条等。

自绘组件只用令牌取色，切换主题后调用 refresh_icons()/update() 即可跟随。
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from ..i18n import tr


# ---------------------------------------------------------------- 文本与数值


def fmt_size(num: float) -> str:
    if not num or num <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    value = float(num)
    while value >= 1024 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return "%d B" % int(value)
    return "%.1f %s" % (value, units[index])


def fmt_speed(num: float) -> str:
    if not num or num <= 0:
        return "--"
    return fmt_size(num) + "/s"


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def fmt_eta(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "--"
    if seconds < 60:
        return tr("{n} 秒", n=seconds)
    if seconds < 3600:
        return tr("{m} 分 {s} 秒", m=seconds // 60, s=seconds % 60)
    return tr("{h} 小时 {m} 分", h=seconds // 3600, m=(seconds % 3600) // 60)


def elide(text: str, width: int, font: Optional[QFont] = None) -> str:
    metrics = QFontMetrics(font) if font else QFontMetrics(QFont())
    return metrics.elidedText(text or "", Qt.ElideRight, width)


# ---------------------------------------------------------------- 基础控件


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, padding: int = 18, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(spacing)
        self.body = layout


class _Chevron(QWidget):
    """折叠箭头：展开朝下、折叠朝右，转角带动画。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._angle = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, value) -> None:
        self._angle = float(value)
        self.update()

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        target = 0.0 if expanded else -90.0
        if abs(target - self._angle) < 0.5:
            return
        duration = theme.motion(180)
        self._anim.stop()
        if not animate or duration <= 0:
            self._on_value(target)
            return
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._angle)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(theme.TEXT_DIM)
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.rotate(self._angle)
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        path.moveTo(-4.4, -2.2)
        path.lineTo(0.0, 2.4)
        path.lineTo(4.4, -2.2)
        painter.drawPath(path)
        painter.end()


class _PanelHeader(QWidget):
    """设置面板的标题行：整行可点，悬停有底色。"""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        text_block = QVBoxLayout()
        text_block.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("H3")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_block.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("Faint")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.subtitle_label.setVisible(bool(subtitle))
        text_block.addWidget(self.subtitle_label)
        layout.addLayout(text_block, 1)
        self.chevron = _Chevron(self)
        layout.addWidget(self.chevron, 0, Qt.AlignTop)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if not self._hover:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        fill = QColor(theme.TEXT)
        fill.setAlphaF(0.045 if not theme.is_dark() else 0.05)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 8, 8)
        painter.end()


class CollapsiblePanel(QFrame):
    """带折叠动画的面板：点标题行展开/收起，高度用动画过渡（类似网页手风琴）。"""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        expanded: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._expanded = bool(expanded)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_height)
        self._anim.finished.connect(self._on_anim_done)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)
        self.header = _PanelHeader(title, subtitle, self)
        self.header.clicked.connect(self.toggle)
        outer.addWidget(self.header)

        self.content = QWidget(self)
        self.content.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.content)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(14)
        outer.addWidget(self.content)

        self.header.chevron.set_expanded(self._expanded, animate=False)
        if not self._expanded:
            self.content.setMaximumHeight(0)
            self.content.setVisible(False)

    # ---------- 状态 ----------
    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.header.chevron.set_expanded(expanded, animate=animate)
        duration = theme.motion(210)
        self._anim.stop()
        if expanded:
            self.content.setVisible(True)
            start = 0 if self.content.maximumHeight() <= 0 else max(0, self.content.height())
            self.content.setMaximumHeight(start)
            target = self._content_height()
        else:
            start = max(0, self.content.height())
            self.content.setMaximumHeight(start)
            target = 0
        if not animate or duration <= 0:
            self._on_height(target)
            self._on_anim_done()
            self.toggled.emit(expanded)
            return
        self._anim.setDuration(duration)
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.start()
        self.toggled.emit(expanded)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    # ---------- 动画 ----------
    def _content_height(self) -> int:
        layout = self.content.layout()
        if layout is not None:
            height = layout.sizeHint().height()
            if height > 0:
                return height
        return max(0, self.content.sizeHint().height())

    def _on_height(self, value) -> None:
        self.content.setMaximumHeight(max(0, int(value)))

    def _on_anim_done(self) -> None:
        if self._expanded:
            self.content.setMaximumHeight(16777215)
        else:
            self.content.setVisible(False)
            self.content.setMaximumHeight(0)



def make_button(
    text: str,
    primary: bool = False,
    icon_name: str = "",
    icon_color: str = "",
    icon_size: int = 16,
    height: int = 36,
    parent: QWidget | None = None,
) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("Primary" if primary else "Ghost")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(height)
    if icon_name:
        color = icon_color or (theme.ON_ACCENT if primary else theme.TEXT)
        button.setIcon(icons.icon(icon_name, icon_size, color))
        button.setIconSize(QSize(icon_size, icon_size))
        button._icon_name = icon_name  # type: ignore[attr-defined]
        button._icon_color = color  # type: ignore[attr-defined]
        button._icon_size = icon_size  # type: ignore[attr-defined]
    return button


class FadeScrim(QWidget):
    """轻量淡入遮罩：只画一块半透明底色，避免对整棵子树逐帧重绘。

    `QGraphicsOpacityEffect` 会让整个控件树走离屏渲染，卡片一多就掉帧；
    这里改成在内容上面盖一层纯色蒙版动画透明度，一帧只画一个矩形。
    """

    def __init__(self, target: QWidget) -> None:
        super().__init__(target)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._alpha = 1.0
        self._color = QColor(theme.BG)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._set_alpha)

    def start(self, duration: int) -> None:
        self.setGeometry(self.parentWidget().rect())
        self._alpha = 0.92
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(0.92)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()

    def _set_alpha(self, value) -> None:
        self._alpha = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        color = QColor(self._color)
        color.setAlphaF(self._alpha)
        painter.fillRect(self.rect(), color)
        painter.end()


class SnapshotFade(QWidget):
    """页面切换：把旧页面截图盖在新页面上淡出。

    逐帧只合成一张位图，比给整个页面挂不透明度效果便宜得多，也不会让
    新页面的子控件跟着重绘，观感是干净的交叉淡入。
    """

    def __init__(self, parent: QWidget, pixmap: QPixmap) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._pixmap = pixmap
        self._alpha = 1.0
        self.setGeometry(parent.rect())
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._set_alpha)

    def start(self, duration: int) -> None:
        self.show()
        self.raise_()
        self._anim.setDuration(duration)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()

    def _set_alpha(self, value) -> None:
        self._alpha = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.end()


def fade_in(widget: QWidget, duration: int = 200, delay: int = 0) -> None:
    """整块内容淡入：盖一层蒙版淡出，结束即销毁。"""
    if widget is None:
        return
    span = theme.motion(duration)
    if span <= 0:
        return

    def start() -> None:
        try:
            scrim = FadeScrim(widget)
            scrim.start(span)
        except RuntimeError:
            return

    if delay > 0:
        QTimer.singleShot(theme.motion(delay), start)
    else:
        start()


def recolor_button_icon(button, color: str) -> None:
    """把 make_button 烘进去的图标按新颜色重画（侧栏选中态用）。"""
    name = getattr(button, "_icon_name", "")
    if not name:
        return
    size = int(getattr(button, "_icon_size", 16) or 16)
    button._icon_color = color
    button.setIcon(icons.icon(name, size=size, color=theme.relabel(color)))


def refresh_icons(root: QWidget) -> None:
    """主题切换后重刷烘焙进图标的颜色（图标是位图，不会跟着 QSS 变色）。"""
    for widget in root.findChildren(QWidget):
        refresh_theme = getattr(widget, "refresh_theme", None)
        if callable(refresh_theme):
            refresh_theme()
    for button in root.findChildren(QPushButton):
        refresh = getattr(button, "refresh_icons", None)
        if callable(refresh):
            refresh()
            continue
        name = getattr(button, "_icon_name", "")
        if not name:
            continue
        size = getattr(button, "_icon_size", 16)
        color = theme.relabel(getattr(button, "_icon_color", theme.TEXT))
        button._icon_color = color  # type: ignore[attr-defined]
        button.setIcon(icons.icon(name, size, color))
        button.setIconSize(QSize(size, size))


class IconButton(QPushButton):
    def __init__(
        self,
        name: str,
        size: int = 32,
        icon_size: int = 18,
        color: str = theme.TEXT_DIM,
        hover_color: str = theme.TEXT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(icons.icon(name, icon_size, color))
        self.setIconSize(QSize(icon_size, icon_size))
        self._hover = QColor(theme.relabel(hover_color))
        self._hover_icon = icons.icon(name, icon_size, hover_color)
        self._idle_icon = icons.icon(name, icon_size, color)
        self._name = name
        self._icon_size = icon_size
        self._hover_color = hover_color
        self._idle_color = color

    def refresh_icons(self) -> None:
        self._idle_color = theme.relabel(self._idle_color)
        self._hover_color = theme.relabel(self._hover_color)
        self._hover = QColor(self._hover_color)
        self._idle_icon = icons.icon(self._name, self._icon_size, self._idle_color)
        self._hover_icon = icons.icon(self._name, self._icon_size, self._hover_color)
        self.setIcon(self._hover_icon if self.underMouse() else self._idle_icon)

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self._idle_icon = icons.icon(name, self._icon_size, self._idle_color)
        self._hover_icon = icons.icon(name, self._icon_size, self._hover_color)
        self.setIcon(self._hover_icon if self.underMouse() else self._idle_icon)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setIcon(self._idle_icon)
        super().leaveEvent(event)


class CheckToggle(QWidget):
    """自绘勾选行：方框 + 勾号都用 QPainter 画，勾选状态带 150ms 过渡。"""

    toggled = Signal(bool)

    def __init__(self, text: str, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._checked = bool(checked)
        self._hover = False
        self._progress = 1.0 if self._checked else 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _animate_to(self, target: float) -> None:
        duration = theme.motion(150)
        if duration <= 0:
            self._progress = target
            self.update()
            return
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self._text = text
        self.update()

    def isChecked(self) -> bool:  # noqa: N802 - Qt 命名
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt 命名
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._animate_to(1.0 if checked else 0.0)
        self.toggled.emit(checked)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.setChecked(not self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        box = 18.0
        rect = QRectF(1.0, (self.height() - box) / 2.0, box, box)
        path = QPainterPath()
        path.addRoundedRect(rect, 5.5, 5.5)
        progress = max(0.0, min(1.0, self._progress))

        if progress > 0.002:
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(progress)
            painter.fillPath(path, fill)
        if progress < 0.999:
            border = QColor(theme.TEXT)
            border.setAlphaF((0.34 if self._hover else 0.20) if not theme.is_dark() else (0.44 if self._hover else 0.25))
            painter.setPen(QPen(border, 1.4))
            painter.drawPath(path)
        if progress > 0.01:
            painter.save()
            painter.setOpacity(progress)
            icons.paint_icon(
                painter,
                "check",
                rect.adjusted(3.6, 3.6, -3.6, -3.6),
                QColor(theme.ON_ACCENT),
                2.0,
            )
            painter.restore()

        painter.setPen(QColor(theme.TEXT if self._checked or self._hover else theme.TEXT_DIM))
        font = self.font()
        font.setPointSizeF(9.75)
        painter.setFont(font)
        painter.drawText(
            QRectF(box + 11, 0, self.width() - box - 11, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._text,
        )
        painter.end()


class TagLabel(QLabel):
    def __init__(self, text: str, color: str = theme.TEXT_DIM, bg_alpha: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._color = color
        self._bg_alpha = bg_alpha
        self.refresh_theme()

    def refresh_theme(self) -> None:
        bg = theme.HOVER_MID if self._bg_alpha <= 20 else theme.HOVER_STRONG
        if self._bg_alpha <= 10:
            bg = theme.FILL
        self.setStyleSheet(
            "QLabel { color: %s; background-color: %s;"
            " border-radius: 9px; padding: 3px 9px; font-size: 11px; }" % (theme.relabel(self._color), bg)
        )


class SectionHeader(QWidget):
    more_clicked = Signal()

    def __init__(self, title: str, subtitle: str = "", more_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("H2")
        block.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("Faint")
        self.subtitle.setVisible(bool(subtitle))
        block.addWidget(self.subtitle)
        layout.addLayout(block)
        layout.addStretch(1)
        self.more = QPushButton(more_text)
        self.more.setObjectName("Subtle")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setVisible(bool(more_text))
        self.more.clicked.connect(self.more_clicked)
        layout.addWidget(self.more)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))


class Spinner(QWidget):
    """细圆环加载指示器。"""

    def __init__(self, size: int = 22, color: str = theme.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._angle = (self._angle + 8) % 360
        self.update()

    def start(self) -> None:
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = min(self.width(), self.height()) - 4
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pen = QPen(QColor(theme.TRACK_STRONG), 2.2)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen = QPen(self._color, 2.2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int(self._angle * 16), int(100 * 16))
        painter.end()


class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        description: str = "",
        icon_name: str = "film",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 40, 0, 40)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)
        self.icon_name = icon_name
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setFixedHeight(52)
        layout.addWidget(self._icon)
        self.title = QLabel(title)
        self.title.setObjectName("H3")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setObjectName("Muted")
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self._paint_icon()

    def _paint_icon(self) -> None:
        pixmap = icons.icon_pixmap(self.icon_name, 44, theme.TEXT_FAINT, 1.4)
        self._icon.setPixmap(pixmap)


# ---------------------------------------------------------------- 封面与卡片


class CoverLabel(QWidget):
    """圆角封面：等比裁切填充，加载完成前显示渐隐占位。"""

    def __init__(
        self,
        width: int = 158,
        height: int = 222,
        radius: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._radius = radius
        self._image: Optional[QImage] = None
        self._placeholder = ""
        self._hover = False
        self._pixmap: Optional[QPixmap] = None
        self._fade = 1.0
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.valueChanged.connect(self._on_fade)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_image(self, image: Optional[QImage]) -> None:
        new_image = image is not None and not image.isNull()
        had_image = self._image is not None and not self._image.isNull()
        self._image = image
        self._pixmap = None
        self.update()
        if new_image and not had_image:
            # 封面从占位块淡入，避免生硬地「啪」一下出现
            duration = theme.motion(180)
            self._fade_anim.stop()
            if duration <= 0:
                self._fade = 1.0
                self.update()
                return
            self._fade = 0.0
            self._fade_anim.setDuration(duration)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.start()

    def _on_fade(self, value) -> None:
        self._fade = float(value)
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def set_hover(self, value: bool) -> None:
        if self._hover != value:
            self._hover = value
            self.update()

    def _scaled(self) -> Optional[QPixmap]:
        if self._image is None or self._image.isNull():
            return None
        if self._pixmap is None:
            target = self.size()
            scaled = self._image.scaled(
                target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            self._pixmap = QPixmap.fromImage(scaled)
        return self._pixmap

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)

        pixmap = self._scaled()
        if pixmap is None:
            gradient = QLinearGradient(0, 0, rect.width(), rect.height())
            gradient.setColorAt(0.0, QColor(theme.COVER_TOP))
            gradient.setColorAt(1.0, QColor(theme.COVER_BOTTOM))
            painter.fillRect(rect, gradient)
            if self._placeholder:
                tint = QColor(theme.TEXT)
                tint.setAlphaF(0.30 if theme.is_dark() else 0.45)
                painter.setPen(tint)
                font = QFont(theme.FONT_STACK, 26)
                font.setWeight(QFont.DemiBold)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignCenter, self._placeholder[:1])
        else:
            x = (rect.width() - pixmap.width()) / 2
            y = (rect.height() - pixmap.height()) / 2
            painter.setOpacity(max(0.0, min(1.0, self._fade)))
            painter.drawPixmap(int(x), int(y), pixmap)
            painter.setOpacity(1.0)

        # 底部渐隐，保证叠字清晰
        shade = QLinearGradient(0, rect.height() * 0.55, 0, rect.height())
        shade.setColorAt(0.0, QColor(6, 8, 12, 0))
        shade.setColorAt(1.0, QColor(6, 8, 12, 150 if theme.is_dark() else 110))
        painter.fillRect(rect, shade)

        painter.setClipping(False)
        line = QColor(theme.TEXT)
        line.setAlphaF(0.10 if theme.is_dark() else 0.14)
        pen = QPen(line, 1)
        if self._hover:
            pen = QPen(QColor(theme.ACCENT), 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class PosterCard(QWidget):
    """海报卡：封面 + 标题 + 副标题，hover 时描边提亮。"""

    activated = Signal(str)

    CARD_W = 158
    CARD_H = 222

    def __init__(
        self,
        vod_id: str,
        name: str,
        cover: str = "",
        subtitle: str = "",
        note: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.vod_id = vod_id
        self._hover = False
        self._hover_t = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_anim)
        self.setFixedWidth(self.CARD_W + 8)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        self.cover = CoverLabel(self.CARD_W, self.CARD_H, 12)
        self.cover.set_placeholder(name)
        layout.addWidget(self.cover, 0, Qt.AlignHCenter)
        self.note_label = QLabel(self.cover)
        self.note_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.note_label.hide()
        self.title_label = QLabel(self)
        self.title_label.setObjectName("H3")
        self.title_label.setFixedWidth(self.CARD_W)
        layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setObjectName("Faint")
        self.subtitle_label.setObjectName("CardMeta")
        self.subtitle_label.setFixedWidth(self.CARD_W)
        layout.addWidget(self.subtitle_label)
        layout.addStretch(1)

        self.setToolTip(name)
        self._name = name
        self._cover = cover
        self._note = note
        self.refresh_theme()
        self.set_name(name)
        self.set_subtitle(subtitle)
        self.set_note(note)

    def refresh_theme(self) -> None:
        self.title_label.setText(self.title_label.text())
        self.title_label.setObjectName("CardTitle")
        self.subtitle_label.setObjectName("CardMeta")
        self.note_label.setStyleSheet(
            "QLabel { background: %s; color: %s; border-radius: 6px;"
            " padding: 2px 7px; font-size: 10.5px; font-weight: 600; }" % (theme.BADGE_BG, theme.ON_ACCENT)
        )
        self.update()

    def _on_hover_anim(self, value) -> None:
        self._hover_t = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        duration = theme.motion(130)
        if duration <= 0:
            self._hover_t = target
            self.update()
            return
        self._hover_anim.stop()
        self._hover_anim.setDuration(duration)
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    # ---------- 属性 ----------
    def set_name(self, name: str) -> None:
        self._name = name
        self.title_label.setText(elide(name, self.CARD_W))
        self.cover.set_placeholder(name)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(elide(subtitle, self.CARD_W))
        self.subtitle_label.setVisible(bool(subtitle))

    def set_note(self, note: str) -> None:
        self._note = note
        if not note:
            self.note_label.hide()
            return
        self.note_label.setText(note)
        self.note_label.adjustSize()
        width = self.note_label.width()
        height = self.note_label.height()
        self.note_label.setGeometry(
            self.cover.width() - width - 8,
            self.cover.height() - height - 8,
            width,
            height,
        )
        self.note_label.show()
        self.note_label.raise_()

    def set_cover_url(self, url: str) -> None:
        self._cover = url

    def apply_image(self, image: QImage) -> None:
        self.cover.set_image(image)

    # ---------- 交互 ----------
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = True
        self.cover.set_hover(True)
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hover = False
        self.cover.set_hover(False)
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.vod_id)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = max(0.0, min(1.0, self._hover_t))
        if hover > 0.002:
            border = QColor(theme.TEXT)
            border.setAlphaF((0.16 if not theme.is_dark() else 0.22) * hover)
            fill = QColor(theme.TEXT)
            fill.setAlphaF((0.05 if theme.is_dark() else 0.035) * hover)
            painter.setPen(QPen(border, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 16, 16)
            painter.setPen(Qt.NoPen)
            accent = QColor(theme.ACCENT)
            accent.setAlphaF(0.85 * hover)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(0, self.CARD_H + 4, 3, 46), 1.5, 1.5)
        painter.end()


def attach_cover(card: PosterCard, images) -> None:
    """把封面图异步绑到卡片上（缓存命中立即显示）。"""
    url = card._cover
    if not url:
        return
    cached = images.request(url)
    if cached is not None:
        card.apply_image(cached)
        return

    def on_ready(ready_url: str, image: QImage) -> None:
        if ready_url != url:
            return
        try:
            card.apply_image(image)
        except RuntimeError:
            pass

    images.subscribe(url, on_ready)


class RowScroller(QWidget):
    """横向滚动行：卡片横排、左右箭头翻页、隐藏横向滚动条。"""

    activated = Signal(str)

    def __init__(self, card_width: int = 166, spacing: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._card_width = card_width
        self._cards: list[PosterCard] = []
        self.setMinimumHeight(300)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.inner = QWidget()
        self.inner.setStyleSheet("background: transparent;")
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(spacing)
        self.row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll)

        self._left = IconButton("chevron-left", 30, 16, theme.ON_ACCENT)
        self._right = IconButton("chevron-right", 30, 16, theme.ON_ACCENT)
        for button in (self._left, self._right):
            button.setParent(self)
            button.raise_()
            button.hide()
        self.refresh_theme()
        self._left.clicked.connect(lambda: self._page(-1))
        self._right.clicked.connect(lambda: self._page(1))
        self._anim: Optional[QPropertyAnimation] = None
        self._empty = EmptyState("暂无内容", "", "film", self)
        self._empty.hide()

    def refresh_theme(self) -> None:
        skin = (
            "QPushButton#IconButton { background-color: %s;"
            " border: 1px solid %s; border-radius: 15px; }"
            "QPushButton#IconButton:hover { background-color: %s; border-color: %s; }"
            % (
                theme.alpha(theme.SURFACE, 232) if theme.is_dark() else theme.alpha("#FFFFFF", 235),
                theme.BORDER_STRONG,
                theme.ACCENT,
                theme.ACCENT,
            )
        )
        for button in (self._left, self._right):
            button.setStyleSheet(skin)

    # ---------- 内容 ----------
    def clear(self) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # 只隐藏再销毁：setParent(None) 会把卡片变成顶层窗口，
                # Windows 上会看到好几个小窗闪一下，正是「弹窗」的来源。
                widget.hide()
                widget.deleteLater()
        self._cards.clear()

    def add_card(self, card: PosterCard) -> None:
        card.activated.connect(self.activated)
        self._cards.append(card)
        self.row.addWidget(card)

    def set_items(self, items: Iterable, images=None) -> None:
        """items 为 PosterItem 序列（或具备 vod_id/name/cover/subtitle 的对象）。"""
        self.clear()
        count = 0
        for item in items:
            card = PosterCard(
                str(getattr(item, "vod_id", "")),
                getattr(item, "name", "") or "",
                getattr(item, "cover", "") or "",
                getattr(item, "subtitle", "") or "",
                getattr(item, "note", "") or "",
                self.inner,
            )
            self.add_card(card)
            if images is not None and card._cover:
                self._bind_image(card, images)
            count += 1
        self._empty.setVisible(count == 0)
        if count:
            self.row.addStretch(1)
        self._update_arrows()

    def _bind_image(self, card: PosterCard, images) -> None:
        attach_cover(card, images)

    # ---------- 滚动 ----------
    def _page(self, direction: int) -> None:
        bar = self.scroll.horizontalScrollBar()
        step = max(320, self.scroll.viewport().width() - 120)
        target = bar.value() + direction * step
        target = max(bar.minimum(), min(bar.maximum(), target))
        animation = QPropertyAnimation(bar, b"value", self)
        animation.setDuration(280)
        animation.setStartValue(bar.value())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = animation

    def _update_arrows(self) -> None:
        bar = self.scroll.horizontalScrollBar()
        has_overflow = bar.maximum() > 4
        self._left.setVisible(has_overflow and bar.value() > 4)
        self._right.setVisible(has_overflow and bar.value() < bar.maximum() - 4)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        height = self.height()
        self._left.move(8, (height - 30) // 2)
        self._right.move(self.width() - 38, (height - 30) // 2)
        self._empty.setGeometry(0, 0, self.width(), self.height())
        self._update_arrows()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        bar = self.scroll.horizontalScrollBar()
        if bar.maximum() > 4:
            self._left.setVisible(bar.value() > 4)
            self._right.setVisible(bar.value() < bar.maximum() - 4)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._left.hide()
        self._right.hide()
        super().leaveEvent(event)


# ---------------------------------------------------------------- 提示与浮层


class Toast(QWidget):
    """底部居中的轻量提示条，2.6 秒后淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 18, 10)
        layout.setSpacing(9)
        self._icon = QLabel(self)
        layout.addWidget(self._icon)
        self._text = QLabel(self)
        self._text.setObjectName("ToastText")
        layout.addWidget(self._text)
        self.refresh_theme()
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._fade: Optional[QPropertyAnimation] = None

    def refresh_theme(self) -> None:
        self._text.setObjectName("ToastText")
        self.setStyleSheet(
            "Toast { background-color: %s; border: 1px solid %s; border-radius: 12px; }"
            % (theme.alpha(theme.SURFACE_HI, 246), theme.BORDER)
        )

    def show_message(self, text: str, icon_name: str = "check", color: str = theme.GREEN, msec: int = 2600) -> None:
        text = tr(text)
        self._icon.setPixmap(icons.icon_pixmap(icon_name, 16, color))
        self._text.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        x, y = 12, 12
        if parent is not None:
            x = max(12, (parent.width() - self.width()) // 2)
            y = max(12, parent.height() - self.height() - 30)
        duration = theme.motion(190)
        self._timer.stop()
        self.show()
        self.raise_()
        self.move(x, y + 10 if duration else y)
        self._effect.setOpacity(0.0 if duration else 1.0)
        if duration:
            fade = QPropertyAnimation(self._effect, b"opacity", self)
            fade.setDuration(duration)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            fade.start(QPropertyAnimation.DeleteWhenStopped)
            slide = QPropertyAnimation(self, b"pos", self)
            slide.setDuration(duration)
            slide.setStartValue(self.pos() + QPoint(0, 10))
            slide.setEndValue(QPoint(x, y))
            slide.setEasingCurve(QEasingCurve.OutCubic)
            slide.start(QPropertyAnimation.DeleteWhenStopped)
        self._timer.start(msec)

    def _fade_out(self) -> None:
        duration = theme.motion(220)
        if duration <= 0:
            self.hide()
            return
        hide_anim = QPropertyAnimation(self, b"pos", self)
        hide_anim.setDuration(duration)
        hide_anim.setStartValue(self.pos())
        hide_anim.setEndValue(self.pos() + QPoint(0, 8))
        hide_anim.setEasingCurve(QEasingCurve.InCubic)
        hide_anim.start(QPropertyAnimation.DeleteWhenStopped)
        animation = QPropertyAnimation(self._effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.InCubic)
        animation.finished.connect(self.hide)
        animation.start(QPropertyAnimation.DeleteWhenStopped)
        self._fade = animation


class BusyOverlay(QWidget):
    """局部加载遮罩：半透明底 + 圆环。"""

    def __init__(self, parent: QWidget | None = None, text: str = "加载中") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "BusyOverlay { background-color: %s; }"
            % (theme.alpha(theme.BG, 178) if theme.is_dark() else theme.alpha("#FFFFFF", 190))
        )
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)
        self.spinner = Spinner(28, theme.ACCENT, self)
        layout.addWidget(self.spinner, 0, Qt.AlignHCenter)
        self.label = QLabel(text)
        self.label.setObjectName("Muted")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._anim: Optional[QPropertyAnimation] = None
        self.hide()

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            "BusyOverlay { background-color: %s; }"
            % (theme.alpha(theme.BG, 178) if theme.is_dark() else theme.alpha("#FFFFFF", 190))
        )

    def start(self, text: str = "") -> None:
        if text:
            self.label.setText(tr(text))
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.spinner.start()
        duration = theme.motion(160)
        if duration <= 0:
            self._effect.setOpacity(1.0)
            return
        self._effect.setOpacity(0.0)
        animation = QPropertyAnimation(self._effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = animation

    def stop(self) -> None:
        self.spinner.stop()
        self.hide()


class RatingPill(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            "QLabel { color: %s; background-color: %s;"
            " border: 1px solid %s; border-radius: 9px;"
            " padding: 3px 9px; font-size: 11px; font-weight: 600; }"
            % (theme.GOLD, theme.alpha(theme.GOLD, 26), theme.alpha(theme.GOLD, 72))
        )
