"""分类浏览页（片库）：按分类看完整片库，首次访问后缓存、可手动刷新。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.site import PosterItem
from ...i18n import tr
from .. import theme
from ..components import BusyOverlay, EmptyState, PosterCard, attach_cover, make_button, fade_in
from ..flowlayout import FlowLayout

CACHE_LIMIT = 24  # 最多缓存多少页（每页 20~40 部，够来回翻）


class BrowsePage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self.type_id = 0
        self.title_text = ""
        self.page = 1
        self._loading = False
        self._stale = False
        self._cards: list[PosterCard] = []
        self._cache: dict[tuple[int, int], list[PosterItem]] = {}
        self._cache_at: dict[tuple[int, int], int] = {}
        self._cache_clock = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 22, 34, 18)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.eyebrow = QLabel("分类片库")
        self.eyebrow.setObjectName("Eyebrow")
        head.addWidget(self.eyebrow)
        self.cache_tag = QLabel("缓存")
        self.cache_tag.setObjectName("Micro")
        self.cache_tag.setToolTip("本页直接来自内存缓存，点刷新可以重新拉取")
        head.addWidget(self.cache_tag)
        self.cache_tag.hide()
        head.addStretch(1)
        self.page_label = QLabel("")
        self.page_label.setObjectName("Muted")
        head.addWidget(self.page_label)
        self.refresh_button = make_button("刷新", icon_name="refresh", height=32)
        self.refresh_button.setMinimumWidth(80)
        self.refresh_button.setToolTip("重新拉取这个分类的第一页，忽略缓存")
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button)
        self.prev_button = make_button("上一页", icon_name="chevron-left", height=32)
        self.next_button = make_button("下一页", icon_name="chevron-right", height=32)
        for button in (self.prev_button, self.next_button):
            button.setMinimumWidth(96)
            head.addWidget(button)
        self.prev_button.clicked.connect(lambda: self.load_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self.load_page(self.page + 1))
        outer.addLayout(head)

        self.title = QLabel("")
        self.title.setObjectName("H1")
        outer.addWidget(self.title)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        self.content = QWidget()
        self.scroll.setWidget(self.content)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 4, 0, 20)
        self.body.setSpacing(16)
        self.grid_host = QWidget(self.content)
        self.grid = FlowLayout(self.grid_host, margin=0, h_spacing=12, v_spacing=18)
        self.body.addWidget(self.grid_host)
        self.body.addStretch(1)

        self.empty = EmptyState("这个分类暂时没有内容", "", "film", self.content)
        self.body.addWidget(self.empty)
        self.empty.hide()

        self.busy = BusyOverlay(self, "正在加载片库")

    # ---------- 缓存 ----------
    def _cache_enabled(self) -> bool:
        return bool(getattr(self.ctx.settings, "cache_categories", True))

    def _remember(self, type_id: int, page: int, items: list[PosterItem]) -> None:
        if not self._cache_enabled():
            return
        key = (type_id, page)
        self._cache[key] = list(items)
        self._cache_clock += 1
        self._cache_at[key] = self._cache_clock
        while len(self._cache) > CACHE_LIMIT:
            oldest = min(self._cache_at, key=self._cache_at.get)
            self._cache.pop(oldest, None)
            self._cache_at.pop(oldest, None)

    def _drop_cache(self, type_id: Optional[int] = None) -> None:
        if type_id is None:
            self._cache.clear()
            self._cache_at.clear()
            return
        for key in [key for key in self._cache if key[0] == type_id]:
            self._cache.pop(key, None)
            self._cache_at.pop(key, None)

    def on_cache_setting_changed(self) -> None:
        """设置里开关了「分类片库缓存」。"""
        if not self._cache_enabled():
            self._drop_cache()
        self.cache_tag.setVisible(self.cache_tag.isVisible() and self._cache_enabled())

    # ---------- 数据 ----------
    def open_category(self, type_id: int, title: str) -> None:
        changed = type_id != self.type_id
        self.type_id = type_id
        self.title_text = title
        self.title.setText(title)
        if changed:
            self.page = 0
            self.scroll.verticalScrollBar().setValue(0)
        if changed or not self._cards:
            page = 1
            if self._cache_enabled():
                hit = self._cache.get((type_id, page))
                if hit is not None:
                    self.page = page
                    self._render(page, hit, cached=True)
                    return
            self.load_page(page)

    def load_page(self, page: int, force: bool = False) -> None:
        if self._loading or page < 1 or not self.type_id:
            return
        type_id = self.type_id
        self.page = page
        self._stale = False
        if not force and self._cache_enabled():
            hit = self._cache.get((type_id, page))
            if hit is not None:
                self._render(page, hit, cached=True)
                return
        self._loading = True
        self.busy.start(tr("正在加载第 {page} 页", page=page))
        self.ctx.tasks.run(
            lambda: self.ctx.api.listing(type_id, page),
            lambda items: self._on_loaded(type_id, page, items),
            self._on_error,
        )

    def refresh(self) -> None:
        """片库刷新：丢掉当前分类的缓存，从第一页重新拉。"""
        if not self.type_id or self._loading:
            return
        self._drop_cache(self.type_id)
        self.cache_tag.hide()
        self.page = 0
        self.load_page(1, force=True)
        self.ctx.notify(tr("已刷新「{name}」", name=self.title_text or tr("分类")), "refresh", theme.ACCENT)

    def _on_error(self, message: str) -> None:
        self._loading = False
        self.busy.stop()
        self.empty.title.setText(tr("加载失败"))
        self.empty.description.setText(message)
        self.empty.show()
        self.ctx.notify(message, "alert", theme.ACCENT)

    def _on_loaded(self, type_id: int, page: int, items: list[PosterItem]) -> None:
        self._loading = False
        self.busy.stop()
        self._remember(type_id, page, items)
        if type_id != self.type_id or page != self.page:
            return
        self._render(page, items, cached=False)

    def _render(self, page: int, items: list[PosterItem], cached: bool = False) -> None:
        self._clear_grid()
        for item in items:
            card = PosterCard(item.vod_id, item.name, item.cover, item.subtitle, item.note, self.grid_host)
            card.activated.connect(lambda vod_id: self.shell.open_detail(vod_id))
            card.show()
            self.grid.addWidget(card)
            self._cards.append(card)
            attach_cover(card, self.ctx.images)
        self.grid_host.updateGeometry()
        self.empty.setVisible(not items)
        if items:
            self.page_label.setText(tr("第 {page} 页 · 本页 {count} 部", page=page, count=len(items)))
        else:
            self.page_label.setText(tr("这一页没有内容"))
        self.prev_button.setEnabled(page > 1)
        self.next_button.setEnabled(len(items) >= 20)
        self.cache_tag.setVisible(bool(cached) and self._cache_enabled())
        self.scroll.verticalScrollBar().setValue(0)
        fade_in(self.grid_host)

    def _clear_grid(self) -> None:
        for card in self._cards:
            card.hide()  # 不要 setParent(None)：会短暂变成顶层窗口
            card.deleteLater()
        self._cards.clear()

    # ---------- 生命周期 ----------
    def invalidate(self, reload_now: bool = False) -> None:
        """调试遮蔽等全局开关变了：缓存里的名称/封面已经不可信。"""
        self._stale = True
        self._drop_cache()
        self.cache_tag.hide()
        if reload_now and self.type_id:
            self.load_page(self.page, force=True)

    def on_show(self, type_id: Optional[int] = None, title: str = "", **kwargs) -> None:
        if type_id and (type_id != self.type_id or not self._cards):
            self.open_category(type_id, title)
        elif self._stale and self.type_id:
            self.load_page(self.page, force=True)
        elif type_id and not self._cache_enabled():
            # 关掉缓存后，每次回到分类页都重新拉一遍
            self.load_page(self.page, force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.busy.setGeometry(self.rect())
