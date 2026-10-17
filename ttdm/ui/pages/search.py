"""搜索结果页：卡片网格 + 翻页。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.site import PosterItem
from ...i18n import tr
from .. import theme
from ..components import BusyOverlay, EmptyState, PosterCard, attach_cover, make_button, fade_in
from ..flowlayout import FlowLayout


class SearchPage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self.keyword = ""
        self.page = 1
        self._loading = False
        self._stale = False
        self._cards: list[PosterCard] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 22, 34, 18)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.eyebrow = QLabel("搜索结果")
        self.eyebrow.setObjectName("Eyebrow")
        head.addWidget(self.eyebrow)
        head.addStretch(1)
        self.page_label = QLabel("")
        self.page_label.setObjectName("Muted")
        head.addWidget(self.page_label)
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

        self.empty = EmptyState(
            "没有找到相关动漫",
            "换个关键词试试，或在首页的分类里浏览",
            "search",
            self.content,
        )
        self.body.addWidget(self.empty)
        self.empty.hide()

        self.busy = BusyOverlay(self, "正在搜索")

    # ---------- 查询 ----------
    def search(self, keyword: str, page: int = 1) -> None:
        keyword = (keyword or "").strip()
        if not keyword:
            return
        if keyword != self.keyword:
            page = 1
        self.keyword = keyword
        self._stale = False
        self.load_page(page)

    def load_page(self, page: int) -> None:
        if self._loading or page < 1:
            return
        page = max(1, page)
        keyword = self.keyword
        self.page = page
        self._stale = False
        self._loading = True
        self.busy.start(tr("正在搜索「{kw}」", kw=keyword))
        self.ctx.tasks.run(
            lambda: self.ctx.api.search(keyword, page),
            lambda items: self._on_loaded(keyword, page, items),
            self._on_error,
        )

    def _on_error(self, message: str) -> None:
        self._loading = False
        self.busy.stop()
        self.empty.title.setText(tr("搜索失败"))
        self.empty.description.setText(message)
        self.empty.show()
        self.ctx.notify(message, "alert", theme.ACCENT)

    def _on_loaded(self, keyword: str, page: int, items: list[PosterItem]) -> None:
        self._loading = False
        self.busy.stop()
        if keyword != self.keyword:
            return
        self._clear_grid()
        for item in items:
            card = PosterCard(item.vod_id, item.name, item.cover, item.subtitle, item.note, self.grid_host)
            card.activated.connect(lambda vod_id: self.shell.open_detail(vod_id))
            card.show()
            self.grid.addWidget(card)
            self._cards.append(card)
            attach_cover(card, self.ctx.images)
        self.grid_host.updateGeometry()
        self.title.setText(tr("「{kw}」", kw=keyword))
        if items:
            self.empty.hide()
            count = len(items)
            self.page_label.setText(
                tr("第 {page} 页 · 本页 {count} 部", page=page, count=count)
                + (tr("，可能还有更多") if count >= 36 else "")
            )
        else:
            self.empty.title.setText(tr("没有找到相关动漫"))
            self.empty.description.setText(tr("换个关键词试试，或在首页的分类里浏览"))
            self.empty.show()
            self.page_label.setText(tr("没有结果") if page == 1 else tr("没有更多结果"))
        self.prev_button.setEnabled(page > 1)
        self.next_button.setEnabled(len(items) >= 20)
        self.scroll.verticalScrollBar().setValue(0)
        fade_in(self.grid_host)

    def _clear_grid(self) -> None:
        for card in self._cards:
            card.hide()  # 不要 setParent(None)：那样会生成顶层窗口，屏幕上闪一下
            card.deleteLater()
        self._cards.clear()

    # ---------- 生命周期 ----------
    def invalidate(self, reload_now: bool = False) -> None:
        """调试遮蔽这类全局开关变了：结果已经作废，下次进来重新请求。"""
        self._stale = True
        if reload_now and self.keyword:
            self.load_page(self.page)

    def on_show(self, keyword: Optional[str] = None, **kwargs) -> None:
        if keyword and keyword != self.keyword:
            self.search(keyword)
        elif self._stale and self.keyword:
            self.load_page(self.page)
        elif not self._cards and self.keyword:
            self.load_page(self.page)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.busy.setGeometry(self.rect())
