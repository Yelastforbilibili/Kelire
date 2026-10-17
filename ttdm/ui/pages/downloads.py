"""下载管理页：进行中任务与已完成记录。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import DownloadRecord
from ...core import masking
from ...core.downloader import DownloadProgress
from ...i18n import tr
from .. import theme
from ..components import (
    CoverLabel,
    EmptyState,
    IconButton,
    fade_in,
    fmt_eta,
    fmt_size,
    fmt_speed,
    make_button,
    SectionHeader,
)

_STATUS_TEXT = {
    "queued": "排队中",
    "downloading": "下载中",
    "finished": "已完成",
    "error": "失败",
    "canceled": "已取消",
}


class DownloadRow(QFrame):
    dismissed = Signal(str)

    def __init__(self, ctx, shell, progress: DownloadProgress) -> None:
        super().__init__()
        self.ctx = ctx
        self.shell = shell
        self.task_id = progress.task_id
        self._status = ""
        self.setObjectName("Row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 16, 12)
        layout.setSpacing(14)

        display_name = masking.name(progress.name)
        self.thumb = CoverLabel(52, 72, 8)
        self.thumb.set_placeholder(display_name)
        layout.addWidget(self.thumb, 0, Qt.AlignTop)

        middle = QVBoxLayout()
        middle.setSpacing(6)
        self.title = QLabel(display_name)
        self.title.setStyleSheet(
            "QLabel { font-size: 13px; font-weight: 600; color: %s; }" % theme.TEXT
        )
        middle.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Faint")
        middle.addWidget(self.subtitle)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        middle.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        middle.addWidget(self.status)
        layout.addLayout(middle, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.percent = QLabel("0%")
        self.percent.setStyleSheet(
            "QLabel { font-size: 16px; font-weight: 700; color: %s; }" % theme.TEXT
        )
        self.percent.setAlignment(Qt.AlignRight)
        right.addWidget(self.percent)
        self.action = IconButton("close", 28, 15, theme.TEXT_DIM, "#FFFFFF")
        self.action.setToolTip("取消下载")
        self.action.clicked.connect(self._on_action)
        right.addWidget(self.action, 0, Qt.AlignRight)
        layout.addLayout(right)

        self.cover_url = masking.cover(progress.poster, progress.name)
        self.apply(progress)

    # ---------- 渲染 ----------
    def apply(self, progress: DownloadProgress) -> None:
        self._status = progress.status
        if progress.status == "downloading":
            percent = progress.percent
            self.percent.setText("%.1f%%" % percent if progress.total else "…")
            self.bar.setValue(int(percent * 10))
            bits = [fmt_speed(progress.speed)]
            if progress.total:
                bits.append("%s / %s" % (fmt_size(progress.downloaded), fmt_size(progress.total)))
            if progress.eta and percent < 96:
                bits.append(tr("剩余 {time}", time=fmt_eta(progress.eta)))
            if percent >= 96:
                bits.append(tr("正在收尾"))
            self.status.setText(" · ".join(bits))
            self.action.set_icon_name("close")
            self.action.setToolTip("取消下载")
        elif progress.status == "finished":
            self.percent.setText("100%")
            self.bar.setValue(1000)
            self.status.setText(tr("已完成 · {size}", size=fmt_size(progress.total)))
            self.action.set_icon_name("folder")
            self.action.setToolTip("打开文件位置")
        elif progress.status == "error":
            self.percent.setText("--")
            self.bar.setValue(0)
            self.status.setText(tr("下载失败：{error}", error=progress.error or tr("未知错误")))
            self.action.set_icon_name("refresh")
            self.action.setToolTip("重试")
        elif progress.status == "canceled":
            self.percent.setText("--")
            self.bar.setValue(0)
            self.status.setText(tr("已取消"))
            self.action.set_icon_name("close")
        else:
            self.percent.setText(tr("排队"))
            self.bar.setValue(0)
            self.status.setText(tr("等待开始"))
            self.action.set_icon_name("close")
            self.action.setToolTip("取消下载")
        self.subtitle.setText("%s · %s" % (progress.source_name, progress.episode_label))

    def _on_action(self) -> None:
        if self._status in ("downloading", "queued", "canceled"):
            self.ctx.downloads.cancel(self.task_id)
            self.dismissed.emit(self.task_id)
        elif self._status == "finished":
            target = self.ctx.downloads.target_of(self.task_id)
            if target:
                self.shell.open_in_explorer(Path(target).parent)
        elif self._status == "error":
            self.ctx.downloads.retry(self.task_id)


class DownloadRowRecord(QFrame):
    """来自历史记录的已完成条目（重启后仍然可见）。"""

    removed = Signal(str)

    def __init__(self, ctx, shell, record: DownloadRecord) -> None:
        super().__init__()
        self.ctx = ctx
        self.shell = shell
        self.record = record
        self.setObjectName("RowMuted")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 16, 12)
        layout.setSpacing(14)
        display_name = masking.name(record.name)
        display_path = masking.path(record.path)
        self.thumb = CoverLabel(52, 72, 8)
        self.thumb.set_placeholder(display_name)
        layout.addWidget(self.thumb, 0, Qt.AlignTop)
        middle = QVBoxLayout()
        middle.setSpacing(6)
        self.title = QLabel(display_name)
        self.title.setObjectName("RowTitle")
        middle.addWidget(self.title)
        self.subtitle = QLabel("%s · %s" % (record.source_name, record.episode))
        self.subtitle.setObjectName("Faint")
        middle.addWidget(self.subtitle)
        path_label = QLabel(display_path)
        path_label.setObjectName("Muted")
        preferred = QFontMetricsHolder.elide(display_path, 520)
        path_label.setText(preferred)
        path_label.setToolTip(display_path)
        middle.addWidget(path_label)
        layout.addLayout(middle, 1)
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        size = QLabel(fmt_size(record.size))
        size.setObjectName("RowMeta")
        size.setAlignment(Qt.AlignRight)
        right.addWidget(size)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.setAlignment(Qt.AlignRight)
        open_button = IconButton("folder", 26, 14, theme.TEXT_DIM, "#FFFFFF")
        open_button.setToolTip("打开文件位置")
        open_button.clicked.connect(lambda: shell.open_in_explorer(Path(record.path).parent))
        actions.addWidget(open_button)
        drop_button = IconButton("trash", 26, 14, theme.TEXT_DIM, theme.ACCENT)
        drop_button.setToolTip("从记录中移除")
        drop_button.clicked.connect(lambda: self.removed.emit(record.task_id))
        actions.addWidget(drop_button)
        right.addLayout(actions)
        layout.addLayout(right)
        self.cover_url = masking.cover(record.cover, record.name)


class QFontMetricsHolder:
    @staticmethod
    def elide(text: str, width: int) -> str:
        from ..components import elide as _elide

        return _elide(text, width)


class DownloadsPage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self._rows: dict[str, DownloadRow] = {}
        self._records: list[DownloadRowRecord] = []
        self._finished_cb = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 22, 34, 16)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel("下载管理")
        title.setObjectName("H1")
        title_block.addWidget(title)
        self.summary = QLabel("暂无下载任务")
        self.summary.setObjectName("Muted")
        title_block.addWidget(self.summary)
        head.addLayout(title_block)
        head.addStretch(1)
        self.folder_button = make_button("下载目录", icon_name="folder", height=36)
        self.folder_button.clicked.connect(lambda: self.shell.open_in_explorer(self.ctx.download_root()))
        head.addWidget(self.folder_button)
        self.cancel_button = make_button("全部取消", height=36)
        self.cancel_button.clicked.connect(self._cancel_all)
        head.addWidget(self.cancel_button)
        self.clear_button = make_button("清除已完成", height=36)
        self.clear_button.clicked.connect(self._clear_finished)
        head.addWidget(self.clear_button)
        outer.addLayout(head)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        self.content = QWidget()
        self.scroll.setWidget(self.content)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 4, 0, 24)
        self.body.setSpacing(12)

        self.active_header = SectionHeader("本会话任务", "多集同时下载会分摊带宽，2 集通常最快")
        self.body.addWidget(self.active_header)
        self.active_host = QWidget(self.content)
        self.active_layout = QVBoxLayout(self.active_host)
        self.active_layout.setContentsMargins(0, 0, 0, 0)
        self.active_layout.setSpacing(10)
        self.body.addWidget(self.active_host)

        self.history_header = SectionHeader("已完成的下载", "记录保存在本机，重启后仍可查看")
        self.body.addWidget(self.history_header)
        self.history_host = QWidget(self.content)
        self.history_layout = QVBoxLayout(self.history_host)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(10)
        self.body.addWidget(self.history_host)
        self.body.addStretch(1)

        self.empty = EmptyState("还没有下载任务", "在作品详情页选择线路后即可下载", "download", self.content)
        self.body.insertWidget(0, self.empty)

        self._load_records()

    # ---------- 历史记录 ----------
    def _load_records(self) -> None:
        records = self.ctx.store.downloads[:60]
        for record in records:
            row = DownloadRowRecord(self.ctx, self.shell, record)
            row.removed.connect(self._drop_record)
            self.history_layout.addWidget(row)
            self._records.append(row)
            fade_in(row, 160)
            thumb_url = masking.cover(record.cover, record.name)
            if thumb_url:
                image = self.ctx.images.request(thumb_url)
                if image is not None:
                    row.thumb.set_image(image)
                else:
                    self._bind_thumb(row, thumb_url)
        self._sync_visibility()

    def _bind_thumb(self, row, url: str) -> None:
        def on_ready(ready_url: str, image: QImage) -> None:
            if ready_url != url:
                return
            try:
                row.thumb.set_image(image)
            except RuntimeError:
                pass

        self.ctx.images.subscribe(url, on_ready)

    def _reload_records(self) -> None:
        for row in self._records:
            self.history_layout.removeWidget(row)
            row.deleteLater()
        self._records.clear()
        self._load_records()

    def _drop_record(self, task_id: str) -> None:
        self.ctx.store.remove_download(task_id)
        self._reload_records()
        self._sync_visibility()

    def _drop_row(self, task_id: str) -> None:
        row = self._rows.pop(task_id, None)
        if row is None:
            return
        self.active_layout.removeWidget(row)
        row.deleteLater()

    def _sync_visibility(self) -> None:
        has_tasks = bool(self._rows)
        has_records = bool(self._records)
        self.active_header.setVisible(has_tasks)
        self.active_host.setVisible(has_tasks)
        self.history_header.setVisible(has_records)
        self.history_host.setVisible(has_records)
        self.empty.setVisible(not has_tasks and not has_records)
        active = [row for row in self._rows.values() if row._status in ("queued", "downloading")]
        records = len(self._records)
        if active:
            self.summary.setText(
                tr("进行中 {active} 个任务 · 已完成 {records} 条记录", active=len(active), records=records)
            )
        elif records:
            self.summary.setText(tr("已完成 {records} 条下载记录", records=records))
        else:
            self.summary.setText(tr("暂无下载任务"))
        self.cancel_button.setEnabled(bool(active))

    # ---------- 事件 ----------
    def on_progress(self, progress: DownloadProgress) -> None:
        row = self._rows.get(progress.task_id)
        if row is None:
            row = DownloadRow(self.ctx, self.shell, progress)
            row.dismissed.connect(self._drop_row)
            self.active_layout.addWidget(row)
            self._rows[progress.task_id] = row
            poster = masking.cover(progress.poster, progress.name)
            if poster:
                image = self.ctx.images.request(poster)
                if image is not None:
                    row.thumb.set_image(image)
            row.show()
            fade_in(row, 200)
        else:
            row.apply(progress)
        if progress.status == "finished":
            self._remember(progress)
            self._drop_row(progress.task_id)
            self._reload_records()
        elif progress.status == "canceled":
            self._drop_row(progress.task_id)
        self._sync_visibility()

    def _remember(self, progress: DownloadProgress) -> None:
        try:
            size = Path(progress.target).stat().st_size if progress.target else progress.total
        except OSError:
            size = progress.total
        record = DownloadRecord(
            task_id=progress.task_id,
            vod_id=progress.vod_id,
            name=progress.name,
            cover=progress.poster,
            episode=progress.episode_label,
            source_name=progress.source_name,
            path=progress.target,
            size=size,
            status="finished",
            finished_at=0,
        )
        self.ctx.store.add_download(record)

    def _cancel_all(self) -> None:
        self.ctx.downloads.cancel_all()

    def _clear_finished(self) -> None:
        self.ctx.downloads.forget_finished()
        active_ids = {
            task_id
            for task_id, row in self._rows.items()
            if row._status in ("queued", "downloading")
        }
        for task_id in list(self._rows):
            if task_id in active_ids:
                continue
            row = self._rows.pop(task_id)
            self.active_layout.removeWidget(row)
            row.deleteLater()
        self.ctx.store.clear_downloads()
        for row in self._records:
            self.history_layout.removeWidget(row)
            row.deleteLater()
        self._records.clear()
        self._sync_visibility()
        self.ctx.notify("已清除完成记录", "check", theme.GREEN)

    # ---------- 生命周期 ----------
    def on_show(self, **kwargs) -> None:
        if len(self.ctx.store.downloads) != len(self._records):
            self._reload_records()
        self._sync_visibility()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
