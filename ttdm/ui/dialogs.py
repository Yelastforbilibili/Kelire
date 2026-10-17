"""通用对话框：下载选集、确认操作。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import masking
from ..core.site import Episode, PlaySource
from ..i18n import tr
from . import theme
from .components import IconButton, make_button
from .flowlayout import FlowLayout


class DownloadPickerDialog(QDialog):
    """让用户在下载前挑集数，默认全选，可一键全不选。"""

    def __init__(
        self,
        parent: Optional[QWidget],
        anime_name: str,
        source: PlaySource,
        folder: Path,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择下载剧集")
        self.setModal(True)
        self.setObjectName("Sheet")

        self.setMinimumSize(720, 460)
        self.resize(800, 560)
        self._source = source
        self._episodes: list[Episode] = list(source.episodes)
        self._buttons: list[QPushButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(14)

        eyebrow = QLabel("下载")
        eyebrow.setObjectName("Eyebrow")
        outer.addWidget(eyebrow)

        title = QLabel(masking.name(anime_name))
        title.setObjectName("H1")
        title.setWordWrap(True)
        outer.addWidget(title)

        subtitle = QLabel(
            tr(
                "{source} · 共 {n} 集 · 保存到 {folder}",
                source=source.name,
                n=len(self._episodes),
                folder=masking.path(str(folder)),
            )
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        for text, handler in (
            ("全选", self._select_all),
            ("全不选", self._select_none),
            ("选择未看", self._select_first_half),
        ):
            button = make_button(text, height=30)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        toolbar.addWidget(self.count_label)
        outer.addLayout(toolbar)

        panel = QFrame(self)
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)

        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(scroll)
        self.grid = FlowLayout(host, margin=0, h_spacing=8, v_spacing=8)
        scroll.setWidget(host)
        scroll.setMinimumHeight(190)
        scroll.setMaximumHeight(430)
        panel_layout.addWidget(scroll)
        outer.addWidget(panel, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)
        cancel = make_button("取消", height=38)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.confirm = make_button("开始下载", primary=True, icon_name="download", height=38)
        self.confirm.clicked.connect(self.accept)
        footer.addWidget(self.confirm)
        outer.addLayout(footer)

        for episode in self._episodes:
            button = QPushButton(self._label(episode))
            button.setObjectName("Episode")
            button.setCheckable(True)
            button.setChecked(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumSize(96, 34)
            button.toggled.connect(self._sync_count)
            button.clicked.connect(self._sync_count)
            self.grid.addWidget(button)
            self._buttons.append(button)
        self._sync_count()

    # ---------- 选中逻辑 ----------
    @staticmethod
    def _label(episode: Episode) -> str:
        text = (episode.label or "").strip()
        return text or tr("第{ep}集", ep=episode.nid)

    def selected(self) -> list[Episode]:
        return [
            episode
            for episode, button in zip(self._episodes, self._buttons)
            if button.isChecked()
        ]

    def _set_all(self, value: bool) -> None:
        for button in self._buttons:
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)
        self._sync_count()

    def _select_all(self) -> None:
        self._set_all(True)

    def _select_none(self) -> None:
        self._set_all(False)

    def _select_first_half(self) -> None:
        for button in self._buttons:
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        count = max(1, len(self._buttons) // 2)
        for button in self._buttons[:count]:
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)
        self._sync_count()

    def _sync_count(self) -> None:
        count = len(self.selected())
        self.count_label.setText(tr("已选 {count} / {total} 集", count=count, total=len(self._episodes)))
        self.confirm.setEnabled(count > 0)


class ConfirmDialog(QDialog):
    """极简确认框，用来做清空数据这类不可撤销操作。"""

    def __init__(
        self,
        parent: Optional[QWidget],
        title: str,
        message: str,
        confirm_text: str = "确定",
        danger: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("Sheet")

        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("H2")
        outer.addWidget(heading)

        body = QLabel(message)
        body.setObjectName("Muted")
        body.setWordWrap(True)
        outer.addWidget(body)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)
        cancel = make_button("取消", height=36)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        ok = make_button(confirm_text, primary=True, height=36)
        if danger:
            ok.setObjectName("Danger")
        ok.clicked.connect(self.accept)
        footer.addWidget(ok)
        outer.addLayout(footer)


def ask_episodes(
    parent: Optional[QWidget],
    anime_name: str,
    source: PlaySource,
    folder: Path,
) -> Optional[Sequence[Episode]]:
    """弹出选集框，返回选中的集数；取消返回 None。"""
    if not source.episodes:
        return None
    dialog = DownloadPickerDialog(parent, anime_name, source, folder)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.selected()
