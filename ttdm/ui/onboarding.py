"""首次使用的两步引导：免责声明（必须停留 5 秒）+ 小贴士。

两块内容都要求用户勾选同意，第一步没读满 5 秒时按钮不可用；两步之间是
淡出 + 横向滑入的过渡，和页面切换用同一套动效节奏。
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import theme
from .components import CheckToggle, make_button

REQUIRED_SECONDS = 5
STEP_HEIGHT = 386

DISCLAIMER = (
    "Kelire 是一个在本机运行的动漫浏览与下载工具，只聚合公开站点上的页面信息，"
    "本身不提供、不存储、也不传播任何视频文件。",
    "所有影片、封面与文字介绍的版权都归原始权利人所有。下载后请于 24 小时内删除，"
    "仅用于个人学习与试看。",
    "请勿把本工具用于商业用途、二次传播或其它侵犯版权、违反当地法律法规的行为。"
    "由此产生的后果由使用者自行承担。",
    "程序按「现状」提供，不附带任何明示或暗示的担保。作者不对使用过程中的数据丢失、"
    "内容侵权或其它损失负责。",
)

TIPS = (
    "本站资源来自第三方站点，其中一部分作品在题材与表现方式上比较大胆，"
    "可能包含暴力、惊悚或成人向的内容，并不适合所有人。",
    "如果你还未成年，请先征得家长或监护人的同意，并尽量在他们的陪同下使用；"
    "遇到让自己不舒服的内容，停下来就好。",
    "追番很容易停不下来，记得给眼睛和作息留点余地：每看一集远眺一会儿，"
    "夜深了就把音量调低一点。",
    "本工具完全在本机运行，不会上传你的观看记录、下载记录与任何个人信息。",
)


class _StepDots(QWidget):
    """两步指示：当前步骤是加长的强调色胶囊。"""

    def __init__(self, count: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._count = count
        self._index = 0
        self.setFixedSize(14 * count + 8 * (count - 1) + 8, 8)

    def set_index(self, index: int) -> None:
        self._index = max(0, min(self._count - 1, index))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        x = 0.0
        for index in range(self._count):
            active = index == self._index
            width = 22.0 if active else 10.0
            color = QColor(theme.ACCENT if active else theme.TEXT_FAINT)
            if not active:
                color.setAlphaF(0.45)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, 1.0, width, 6.0), 3.0, 3.0)
            x += width + 8.0
        painter.end()


class _ProgressLine(QWidget):
    """细进度条：读秒期间匀速走完。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self.setFixedHeight(4)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        track = QColor(theme.TEXT)
        track.setAlphaF(0.10 if theme.is_dark() else 0.09)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 4), 2, 2)
        if self._value > 0.001:
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(0, 0, self.width() * self._value, 4), 2, 2)
        painter.end()


class _StepHost(QWidget):
    """把每一步铺满整块区域，动画只改 x 偏移。"""

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        for child in self.children():
            if isinstance(child, QWidget) and child.parent() is self:
                child.setGeometry(0, 0, self.width(), self.height())


class OverlayStep(QWidget):
    """一步的内容：标签 + 标题 + 正文 + 勾选 + 主按钮。"""

    ready_changed = Signal(bool)
    activated = Signal()

    def __init__(
        self,
        eyebrow: str,
        title: str,
        paragraphs: tuple[str, ...],
        agreement: str,
        action: str,
        countdown: bool = False,
        done_text: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.countdown_done = not countdown
        self._done_text = done_text or tr("已读完，勾选同意后即可继续")
        self.setFixedHeight(STEP_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.eyebrow = QLabel(eyebrow)
        self.eyebrow.setObjectName("Eyebrow")
        layout.addWidget(self.eyebrow)

        self.title = QLabel(title)
        self.title.setObjectName("Display")
        layout.addWidget(self.title)

        self.body_scroll = QScrollArea(self)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.body_scroll.viewport().setAutoFillBackground(False)
        self.body_host = QWidget()
        self.body_host.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.body_host)
        self.body_layout.setContentsMargins(0, 2, 6, 2)
        self.body_layout.setSpacing(9)
        self.paragraphs: list[QLabel] = []
        for text in paragraphs:
            label = QLabel(text)
            label.setObjectName("OnboardBody")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.body_layout.addWidget(label)
            self.paragraphs.append(label)
        self.body_layout.addStretch(1)
        self.body_scroll.setWidget(self.body_host)
        layout.addWidget(self.body_scroll, 1)

        self.progress = _ProgressLine(self)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.agree = CheckToggle(agreement)
        self.agree.toggled.connect(lambda _checked: self._sync())
        layout.addWidget(self.agree)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.action_button = make_button(action, primary=True, height=40)
        self.action_button.setMinimumWidth(148)
        self.action_button.setEnabled(False)
        self.action_button.clicked.connect(self.activated)
        row.addWidget(self.action_button)
        layout.addLayout(row)

    # ---------- 读秒 ----------
    def set_countdown(self, remaining: float, total: float = REQUIRED_SECONDS) -> None:
        done = remaining <= 0
        self.countdown_done = done
        self.progress.set_value(1.0 if done else (1.0 - remaining / max(1.0, total)))
        if done:
            self.status.setText(self._done_text)
        else:
            seconds = max(1, int(remaining + 0.999))
            self.status.setText(tr("请先阅读以上内容，还需 {n} 秒", n=seconds))
        self._sync()

    def _sync(self) -> None:
        self.action_button.setEnabled(self.countdown_done and self.agree.isChecked())
        self.ready_changed.emit(self.action_button.isEnabled())


class OnboardingOverlay(QWidget):
    """铺满主窗口的引导层：底部免责声明 / 上部小贴士。"""

    finished = Signal()
    declined = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("OnboardingScrim")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._index = 0
        self._remaining = float(REQUIRED_SECONDS)
        self._anim: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        card_row = QHBoxLayout()
        card_row.addStretch(1)
        self.card = QFrame(self)
        self.card.setObjectName("OnboardingCard")
        self.card.setFixedWidth(720)
        card_row.addWidget(self.card)
        card_row.addStretch(1)
        outer.addLayout(card_row)
        outer.addStretch(1)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(30, 26, 30, 20)
        card_layout.setSpacing(16)

        self.step_host = _StepHost(self.card)
        self.step_host.setFixedHeight(STEP_HEIGHT)
        card_layout.addWidget(self.step_host)

        self.steps = [
            OverlayStep(
                tr("首次使用 · 第 1 步"),
                tr("免责声明"),
                DISCLAIMER,
                tr("我已阅读以上条款，并了解使用过程中的相关责任"),
                tr("同意并继续"),
                countdown=True,
                parent=self.step_host,
            ),
            OverlayStep(
                tr("首次使用 · 第 2 步"),
                tr("小贴士"),
                TIPS,
                tr("我已知悉，并会合理安排观看"),
                tr("开始使用"),
                countdown=False,
                done_text=tr("勾选同意后即可开始使用"),
                parent=self.step_host,
            ),
        ]
        for step in self.steps:
            step.hide()
        self.steps[0].activated.connect(self._next)
        self.steps[1].activated.connect(self._finish)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.dots = _StepDots(2, self.card)
        footer.addWidget(self.dots)
        footer.addStretch(1)
        self.quit_button = make_button(tr("退出程序"), height=30)
        self.quit_button.setToolTip(tr("不同意以上内容，关闭程序"))
        self.quit_button.clicked.connect(self.declined)
        footer.addWidget(self.quit_button)
        card_layout.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

        self.refresh_theme()
        self.setFocus(Qt.OtherFocusReason)

    # ---------- 主题 ----------
    def refresh_theme(self) -> None:
        scrim = theme.alpha("#07080C", 214) if theme.is_dark() else theme.alpha("#0B0D14", 148)
        self.setStyleSheet("QWidget#OnboardingScrim { background-color: %s; }" % scrim)
        surface = theme.SURFACE_HOVER if theme.is_dark() else theme.alpha("#FFFFFF", 252)
        border = theme.BORDER_STRONG if theme.is_dark() else theme.BORDER
        self.card.setStyleSheet(
            "QFrame#OnboardingCard { background-color: %s; border: 1px solid %s; border-radius: 18px; }"
            % (surface, border)
        )
        for step in getattr(self, "steps", []):
            for label in step.paragraphs:
                label.setStyleSheet(
                    "QLabel { color: %s; font-size: 12.5px; }" % theme.TEXT_DIM
                )
        self.update()

    # ---------- 步骤 ----------
    def start(self) -> None:
        self._show_step(0, animate=False)
        self._remaining = float(REQUIRED_SECONDS)
        self._started_at = time.monotonic()
        self.steps[0].set_countdown(self._remaining)
        self._timer.start()

    def _tick(self) -> None:
        # 用真实时间算剩余秒数，主线程卡一下也不会让「必须停留 5 秒」变成 7 秒
        elapsed = time.monotonic() - getattr(self, "_started_at", time.monotonic())
        self._remaining = max(0.0, REQUIRED_SECONDS - elapsed)
        self.steps[0].set_countdown(self._remaining)
        if self._remaining <= 0:
            self._timer.stop()

    def _show_step(self, index: int, animate: bool = True) -> None:
        index = max(0, min(len(self.steps) - 1, index))
        previous = self.steps[self._index] if 0 <= self._index < len(self.steps) else None
        target = self.steps[index]
        self._index = index
        self.dots.set_index(index)
        if previous is target:
            target.show()
            return
        duration = theme.motion(230)
        if not animate or duration <= 0:
            for step in self.steps:
                step.hide()
            target.move(0, 0)
            target.setGraphicsEffect(None)
            target.show()
            target.raise_()
            return
        if previous is None:
            target.move(0, 0)
            target.show()
            target.raise_()
            self._fade_in_step(target, duration)
            return

        # 旧的一步向左淡出，新的一步从右侧滑入
        effect = QGraphicsOpacityEffect(previous)
        previous.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", previous)
        fade.setDuration(int(duration * 0.55))
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        slide = QPropertyAnimation(previous, b"pos", previous)
        slide.setDuration(int(duration * 0.55))
        slide.setStartValue(QPoint(0, 0))
        slide.setEndValue(QPoint(-26, 0))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        fade.start(QPropertyAnimation.DeleteWhenStopped)
        slide.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = [fade, slide]

        def enter() -> None:
            previous.hide()
            previous.move(0, 0)
            previous.setGraphicsEffect(None)
            target.move(26, 0)
            target.show()
            target.raise_()
            self._fade_in_step(target, duration)

        QTimer.singleShot(int(duration * 0.55) + 10, enter)

    def _fade_in_step(self, step: QWidget, duration: int) -> None:
        effect = QGraphicsOpacityEffect(step)
        effect.setOpacity(0.0)
        step.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", step)
        fade.setDuration(duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        slide = QPropertyAnimation(step, b"pos", step)
        slide.setDuration(duration)
        slide.setStartValue(step.pos())
        slide.setEndValue(QPoint(0, 0))
        slide.setEasingCurve(QEasingCurve.OutCubic)

        def cleanup() -> None:
            try:
                step.setGraphicsEffect(None)
            except RuntimeError:
                pass

        fade.finished.connect(cleanup)
        fade.start(QPropertyAnimation.DeleteWhenStopped)
        slide.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = [fade, slide]

    def _next(self) -> None:
        if self._index != 0:
            return
        self._timer.stop()
        self._show_step(1)
        self.steps[1].set_countdown(0.0)
        self.steps[1].progress.hide()

    def _finish(self) -> None:
        if self._index != 1:
            return
        duration = theme.motion(200)
        if duration <= 0:
            self.hide()
            self.finished.emit()
            return
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0)
        self.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(duration)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self.hide)
        fade.finished.connect(self.finished)
        fade.start(QPropertyAnimation.DeleteWhenStopped)

    # ---------- 事件 ----------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 遮罩吃掉点击，避免误触后面的页面
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() == Qt.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)
