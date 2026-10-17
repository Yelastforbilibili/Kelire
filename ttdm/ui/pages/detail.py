"""作品详情页：封面信息 + 简介 + 线路切换 + 剧集网格 + 下载入口。"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import HistoryEntry
from ...core.site import AnimeDetail, Episode, PlaySource
from ...i18n import tr
from .. import theme
from ..dialogs import ask_episodes
from ..components import (
    BusyOverlay,
    CoverLabel,
    Divider,
    EmptyState,
    TagLabel,
    fade_in,
    make_button,
)
from ..flowlayout import FlowLayout

_INTRO_COLLAPSE = 78


class DetailPage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self.vod_id = ""
        self.detail: Optional[AnimeDetail] = None
        self.current_sid = 1
        self._play_target: Optional[tuple[int, int, float]] = None
        self._resume: Optional[HistoryEntry] = None
        self._loading = False
        self._stale = False
        self._episode_buttons: list[QPushButton] = []
        self._source_buttons: list[QPushButton] = []
        self._intro_expanded = False

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
        self.body.setContentsMargins(34, 20, 34, 40)
        self.body.setSpacing(20)

        # 顶部返回
        top = QHBoxLayout()
        top.setSpacing(10)
        self.back_button = make_button("返回", icon_name="chevron-left", height=30)
        self.back_button.setMaximumWidth(92)
        self.back_button.clicked.connect(shell.go_back)
        top.addWidget(self.back_button)
        top.addStretch(1)
        self.body.addLayout(top)

        # 主信息区
        hero = QHBoxLayout()
        hero.setSpacing(26)
        self.cover = CoverLabel(226, 318, 16, self.content)
        hero.addWidget(self.cover, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(12)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setObjectName("Display")
        info.addWidget(self.title)

        self.meta_row = QHBoxLayout()
        self.meta_row.setSpacing(8)
        self.meta_row.addStretch(1)
        info.addLayout(self.meta_row)

        self.genre_row = QHBoxLayout()
        self.genre_row.setSpacing(8)
        self.genre_row.addStretch(1)
        info.addLayout(self.genre_row)

        self.facts = QLabel("")
        self.facts.setWordWrap(True)
        self.facts.setStyleSheet("QLabel { color: %s; font-size: 12px; line-height: 170%%; }" % theme.TEXT_DIM)
        info.addWidget(self.facts)

        self.intro = QLabel("")
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet("QLabel { color: %s; font-size: 12.5px; line-height: 175%%; }" % theme.TEXT_DIM)
        info.addWidget(self.intro)
        self.intro_toggle = QPushButton("展开简介")
        self.intro_toggle.setObjectName("Subtle")
        self.intro_toggle.setCursor(Qt.PointingHandCursor)
        self.intro_toggle.clicked.connect(self._toggle_intro)
        self.intro_toggle.hide()
        info.addWidget(self.intro_toggle, 0, Qt.AlignLeft)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.play_button = make_button("立即播放", primary=True, icon_name="play", height=40)
        self.play_button.clicked.connect(self._play_first)
        actions.addWidget(self.play_button)
        self.download_button = make_button("选择剧集下载", icon_name="download", height=40)
        self.download_button.clicked.connect(self._download_all)
        actions.addWidget(self.download_button)
        self.folder_button = make_button("打开目录", icon_name="folder", height=40)
        self.folder_button.clicked.connect(self._open_folder)
        actions.addWidget(self.folder_button)
        actions.addStretch(1)
        info.addLayout(actions)
        info.addStretch(1)
        hero.addLayout(info, 1)
        self.body.addLayout(hero)

        self.body.addWidget(Divider(self.content))

        # 剧集面板
        panel = QFrame(self.content)
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 18, 22, 22)
        panel_layout.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.source_title = QLabel("播放源")
        self.source_title.setObjectName("H2")
        head.addWidget(self.source_title)
        self.source_hint = QLabel("选择线路后开始观看或下载")
        self.source_hint.setObjectName("Faint")
        head.addWidget(self.source_hint)
        head.addStretch(1)
        self.episode_count = QLabel("")
        self.episode_count.setObjectName("Muted")
        head.addWidget(self.episode_count)
        panel_layout.addLayout(head)

        self.tabs_row = QHBoxLayout()
        self.tabs_row.setSpacing(18)
        self.tabs_row.addStretch(1)
        panel_layout.addLayout(self.tabs_row)

        self.episode_host = QWidget(panel)
        self.episode_grid = FlowLayout(self.episode_host, margin=0, h_spacing=8, v_spacing=8)
        panel_layout.addWidget(self.episode_host)
        self.body.addWidget(panel)
        self.body.addStretch(1)

        self.empty = EmptyState("作品不存在或已下架", "", "alert", self.content)
        self.empty.hide()
        self.body.addWidget(self.empty)

        self.busy = BusyOverlay(self, "正在加载详情")

    # ---------- 加载 ----------
    def open(self, vod_id: str, resume: Optional[HistoryEntry] = None) -> None:
        self._resume = resume
        if vod_id != self.vod_id:
            self.detail = None
            self.current_sid = 1
        self.vod_id = vod_id
        self.load()

    def load(self) -> None:
        if self._loading or not self.vod_id:
            return
        self._loading = True
        self._stale = False
        self.busy.start("正在加载详情")
        vod_id = self.vod_id
        self.ctx.tasks.run(
            lambda: self.ctx.api.detail(vod_id),
            lambda detail: self._on_loaded(vod_id, detail),
            self._on_error,
        )

    def _on_error(self, message: str) -> None:
        self._loading = False
        self.busy.stop()
        self.empty.title.setText("详情加载失败")
        self.empty.description.setText(message)
        self.empty.show()
        self.ctx.notify(message, "alert", theme.ACCENT)

    def _on_loaded(self, vod_id: str, detail: AnimeDetail) -> None:
        self._loading = False
        self.busy.stop()
        if vod_id != self.vod_id:
            return
        self.detail = detail
        self.empty.hide()
        self._render(detail)
        self._sync_play_button()
        self.scroll.verticalScrollBar().setValue(0)
        if self._resume is not None:
            resume, self._resume = self._resume, None
            source = detail.source(resume.sid) or (detail.sources[0] if detail.sources else None)
            if source is not None:
                self.select_source(source.sid)
                self._set_play_target(resume.sid, resume.nid or 1, resume.position)
                if self.ctx.settings.resume_on_open:
                    self._open_player(resume.sid, resume.nid or 1, resume.position)

    def _set_play_target(self, sid: int, nid: int, position: float) -> None:
        self._play_target = (int(sid), int(nid), float(position or 0.0))
        self._sync_play_button()

    def _sync_play_button(self) -> None:
        """有观看记录时把主按钮改成「继续播放 第N集」，否则回到「立即播放 第1集」。"""
        target = self._play_target
        if target is None and self.vod_id:
            entry = self.ctx.store.get_history(self.vod_id)
            if entry is not None:
                target = (entry.sid, entry.nid or 1, entry.position)
        if target is None:
            self.play_button.setText(tr("立即播放"))
            return
        source = self.detail.source(target[0]) if self.detail is not None else None
        if source is None:
            self.play_button.setText(tr("立即播放"))
            return
        self.play_button.setText(tr("继续播放 第{n}集", n=target[1]))

    # ---------- 渲染 ----------
    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render(self, detail: AnimeDetail) -> None:
        self.title.setText(detail.name)
        self.cover.set_placeholder(detail.name)
        self._load_cover(detail.cover)

        self._clear_layout(self.meta_row)
        self.meta_row.addStretch(1)
        chips: list[QLabel] = []
        if detail.year:
            chips.append(TagLabel(detail.year, theme.TEXT, 22))
        if detail.region:
            chips.append(TagLabel(detail.region, theme.TEXT, 22))
        if detail.category:
            chips.append(TagLabel(detail.category, theme.TEXT, 22))
        if detail.episode_note:
            chips.append(TagLabel(detail.episode_note, theme.GOLD, 26))
        if detail.updated:
            chips.append(TagLabel(tr("更新 {date}", date=detail.updated), theme.TEXT_DIM, 16))
        for index, chip in enumerate(chips):
            self.meta_row.insertWidget(index, chip)

        self._clear_layout(self.genre_row)
        self.genre_row.addStretch(1)
        for index, genre in enumerate(detail.genres[:8]):
            if genre:
                self.genre_row.insertWidget(index, TagLabel(genre, theme.TEXT_DIM, 14))

        facts = []
        if detail.aliases and detail.aliases not in ("未知", "暂无"):
            facts.append(tr("别名　{value}", value=detail.aliases))
        if detail.director and detail.director not in ("未知", "暂无"):
            facts.append(tr("导演　{value}", value=detail.director))
        if detail.actors and detail.actors not in ("未知", "暂无"):
            facts.append(tr("声优　{value}", value=detail.actors))
        if detail.language and detail.language not in ("未知", "暂无"):
            facts.append(tr("语言　{value}", value=detail.language))
        self.facts.setText("\n".join(facts))

        intro = detail.intro.strip()
        if intro in ("", "暂无简介", "暂无介绍"):
            intro = tr("本站暂未提供该作品的简介。")
        self.intro.setText(intro)
        self._intro_expanded = False
        long_intro = len(intro) > 120
        if long_intro:
            self.intro.setMaximumHeight(_INTRO_COLLAPSE)
            self.intro_toggle.setText(tr("展开简介"))
        else:
            self.intro.setMaximumHeight(16777215)
        self.intro_toggle.setVisible(long_intro)

        self._render_sources(detail)
        self.play_button.setEnabled(bool(detail.sources))
        self.download_button.setEnabled(bool(detail.sources))

    def _load_cover(self, url: str) -> None:
        if not url:
            return
        image = self.ctx.images.request(url)
        if image is not None:
            self.cover.set_image(image)
            return

        def on_ready(ready_url: str, ready_image: QImage) -> None:
            if ready_url != url:
                return
            try:
                self.cover.set_image(ready_image)
            except RuntimeError:
                pass

        self.ctx.images.subscribe(url, on_ready)

    def _render_sources(self, detail: AnimeDetail) -> None:
        self._clear_layout(self.tabs_row)
        self.tabs_row.addStretch(1)
        self._source_buttons.clear()
        for index, source in enumerate(detail.sources):
            button = QPushButton("%s" % source.name)
            button.setObjectName("TabButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(source.sid == self.current_sid or (index == 0 and not detail.source(self.current_sid)))
            button.clicked.connect(lambda _=False, sid=source.sid: self.select_source(sid))
            button.setToolTip(tr("共 {n} 集", n=len(source.episodes)))
            self.tabs_row.insertWidget(index, button)
            self._source_buttons.append(button)
        if detail.sources and not detail.source(self.current_sid):
            self.current_sid = detail.sources[0].sid
        for button, source in zip(self._source_buttons, detail.sources):
            button.setChecked(source.sid == self.current_sid)
        self._render_episodes()

    def select_source(self, sid: int) -> None:
        self.current_sid = sid
        for button, source in zip(self._source_buttons, (self.detail.sources if self.detail else [])):
            button.setChecked(source.sid == sid)
        self._render_episodes()

    def _current_source(self) -> Optional[PlaySource]:
        if not self.detail:
            return None
        return self.detail.source(self.current_sid)

    def _render_episodes(self) -> None:
        for button in self._episode_buttons:
            self.episode_grid.removeWidget(button)
            button.deleteLater()
        self._episode_buttons.clear()
        source = self._current_source()
        if source is None:
            self.episode_count.setText("")
            return
        self.episode_count.setText(tr("{source} · 共 {n} 集", source=source.name, n=len(source.episodes)))
        for episode in source.episodes:
            button = QPushButton(self._short_label(episode))
            button.setObjectName("Episode")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumSize(QSize(88, 34))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, ep=episode: self._episode_menu(ep)
            )
            button.clicked.connect(lambda _=False, ep=episode: self._on_episode_clicked(ep))
            button.setToolTip(tr("{label}\n左键播放 · 右键下载", label=episode.label))
            self.episode_grid.addWidget(button)
            self._episode_buttons.append(button)
        self.episode_host.updateGeometry()
        self._mark_watched_episodes()
        fade_in(self.episode_host, 150)

    @staticmethod
    def _short_label(episode: Episode) -> str:
        text = re.sub(r"\s+", " ", episode.label or "").strip()
        return text or tr("第{ep}集", ep=episode.nid)

    def _mark_watched_episodes(self) -> None:
        entry = self.ctx.store.get_history(self.vod_id)
        if entry is None:
            return
        for button in self._episode_buttons:
            button.setChecked(False)
        source = self._current_source()
        if source is not None and entry.sid == source.sid:
            for button, episode in zip(self._episode_buttons, source.episodes):
                if episode.nid == entry.nid:
                    button.setChecked(True)

    # ---------- 交互 ----------
    def _toggle_intro(self) -> None:
        self._intro_expanded = not self._intro_expanded
        if self._intro_expanded:
            self.intro.setMaximumHeight(16777215)
            self.intro_toggle.setText(tr("收起简介"))
        else:
            self.intro.setMaximumHeight(_INTRO_COLLAPSE)
            self.intro_toggle.setText(tr("展开简介"))

    def _on_episode_clicked(self, episode: Episode) -> None:
        self._open_player(self.current_sid, episode.nid, 0.0)

    def _play_first(self) -> None:
        source = self._current_source()
        if source is None or not source.episodes:
            return
        target = self._play_target
        if target is not None and target[0] == source.sid and source.episode(target[1]) is not None:
            self._open_player(target[0], target[1], target[2])
            return
        self._open_player(source.sid, source.episodes[0].nid, 0.0)

    def _open_player(self, sid: int, nid: int, position: float) -> None:
        if self.detail is None:
            return
        self.shell.open_player(self.detail, sid, nid, position)

    def _episode_menu(self, episode: Episode) -> None:
        menu = QMenu(self)
        play_action = QAction("播放本集", menu)
        play_action.triggered.connect(lambda: self._open_player(self.current_sid, episode.nid, 0.0))
        menu.addAction(play_action)
        download_action = QAction("下载本集", menu)
        download_action.triggered.connect(lambda: self._download([episode]))
        menu.addAction(download_action)
        menu.addSeparator()
        from_here = QAction("从本集开始全部下载", menu)
        from_here.triggered.connect(lambda: self._download_from(episode))
        menu.addAction(from_here)
        menu.exec(self.cursor().pos())

    def _download(self, episodes: list[Episode]) -> None:
        detail = self.detail
        source = self._current_source()
        if detail is None or source is None or not episodes:
            return
        task_ids = self.ctx.downloads.enqueue(detail, source, episodes, self.ctx.download_root())
        if task_ids:
            self.ctx.notify(tr("已加入下载队列：{n} 集", n=len(task_ids)), "download", theme.GREEN)
        else:
            self.ctx.notify(tr("加入下载队列失败，请检查下载目录"), "alert", theme.ACCENT)

    def _download_all(self) -> None:
        detail = self.detail
        source = self._current_source()
        if detail is None or source is None or not source.episodes:
            return
        from ...paths import safe_filename

        folder = self.ctx.download_root() / safe_filename(detail.name)
        episodes = ask_episodes(self, detail.name, source, folder)
        if not episodes:
            return
        self._download(list(episodes))

    def _download_from(self, episode: Episode) -> None:
        source = self._current_source()
        if source is None:
            return
        episodes = [ep for ep in source.episodes if ep.nid >= episode.nid]
        self._download(episodes)

    def _open_folder(self) -> None:
        if self.detail is None:
            return
        from ...paths import safe_filename

        folder = self.ctx.download_root() / safe_filename(self.detail.name)
        folder.mkdir(parents=True, exist_ok=True)
        self.shell.open_in_explorer(folder)

    def refresh_theme(self) -> None:
        self.facts.setStyleSheet(
            "QLabel { color: %s; font-size: 12px; line-height: 170%%; }" % theme.TEXT_DIM
        )
        self.intro.setStyleSheet(
            "QLabel { color: %s; font-size: 12.5px; line-height: 175%%; }" % theme.TEXT_DIM
        )

    # ---------- 生命周期 ----------
    def invalidate(self, reload_now: bool = False) -> None:
        """调试遮蔽/语言变了：已经渲染出来的详情需要重新请求一次。"""
        self._stale = True
        if reload_now and self.vod_id:
            self.load()

    def on_show(self, vod_id: Optional[str] = None, resume: Optional[HistoryEntry] = None, **kwargs) -> None:
        if vod_id:
            reused = vod_id == self.vod_id and self.detail is not None and resume is None
            if not reused:
                # 从播放页退回时复用已加载的详情，不再重新请求一遍
                self.open(vod_id, resume)
            elif self._stale:
                self.load()
        elif self.detail is None and self.vod_id:
            self.load()
        elif self._stale and self.vod_id:
            self.load()
        self._mark_watched_episodes()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.busy.setGeometry(self.rect())
