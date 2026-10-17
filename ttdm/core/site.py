"""天天动漫站点解析：首页推荐、搜索、列表、详情、剧集与播放数据。"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote

from . import masking
from .http import HttpClient, NetworkError

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class PosterItem:
    vod_id: str
    name: str
    cover: str = ""
    note: str = ""
    year: str = ""
    region: str = ""
    genres: list[str] = field(default_factory=list)
    category: str = ""
    actors: str = ""

    @property
    def subtitle(self) -> str:
        parts = [p for p in (self.year, self.region, self.category) if p]
        return " · ".join(parts)


@dataclass
class Episode:
    sid: int
    nid: int
    label: str
    index: int


@dataclass
class PlaySource:
    sid: int
    name: str
    episodes: list[Episode] = field(default_factory=list)

    def episode(self, nid: int) -> Optional[Episode]:
        return next((e for e in self.episodes if e.nid == nid), None)


@dataclass
class AnimeDetail:
    vod_id: str
    name: str
    cover: str = ""
    year: str = ""
    region: str = ""
    category: str = ""
    genres: list[str] = field(default_factory=list)
    aliases: str = ""
    director: str = ""
    actors: str = ""
    language: str = ""
    updated: str = ""
    episode_note: str = ""
    intro: str = ""
    sources: list[PlaySource] = field(default_factory=list)

    def source(self, sid: int) -> Optional[PlaySource]:
        return next((s for s in self.sources if s.sid == sid), None)

    @property
    def first_episode(self) -> Optional[Episode]:
        for source in self.sources:
            if source.episodes:
                return source.episodes[0]
        return None

    @property
    def total_episodes(self) -> int:
        return max((len(s.episodes) for s in self.sources), default=0)


@dataclass
class HomeData:
    featured: list[PosterItem] = field(default_factory=list)
    sections: list[tuple[str, list[PosterItem]]] = field(default_factory=list)


@dataclass
class PlayInfo:
    url: str
    next_url: str = ""
    prev_url: str = ""
    flag: str = ""
    vod_id: str = ""
    sid: int = 1
    nid: int = 1


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_VOD_HREF_RE = re.compile(r"/vod/(\d+)/")
_SPACE_RE = re.compile(r"\s+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SLASH_RE = re.compile(r"(?:\s*/\s*)+")


def strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    text = html_lib.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def clean_list(text: str) -> str:
    """把站点用斜杠/逗号分隔的一串名字整理成人类可读形式。"""
    text = _SLASH_RE.sub(" / ", (text or "").strip())
    return text.strip(" /")


def first_text(fragment: str) -> str:
    """取片段中第一段可见文字（用于分离中文标题与英文小字）。"""
    chunk = re.sub(r"<[^>]+>", "\x00", fragment or "")
    for part in chunk.split("\x00"):
        part = html_lib.unescape(part).strip()
        if part:
            return part
    return ""


def parse_attrs(raw: str) -> dict[str, str]:
    return {k.lower(): html_lib.unescape(v) for k, v in _ATTR_RE.findall(raw)}


# ---------------------------------------------------------------------------
# 列表卡片
# ---------------------------------------------------------------------------


def parse_poster_items(fragment: str) -> list[PosterItem]:
    """解析 module-poster-item 卡片（首页/分类/榜单通用）。"""
    items: list[PosterItem] = []
    for match in _ANCHOR_RE.finditer(fragment):
        attrs = parse_attrs(match.group(1))
        if "module-poster-item" not in attrs.get("class", ""):
            continue
        vod = _VOD_HREF_RE.search(attrs.get("href", ""))
        if not vod:
            continue
        body = match.group(2)
        cover = ""
        cover_match = re.search(r'data-original="([^"]+)"', body) or re.search(
            r'<img[^>]+src="(http[^"]+)"', body
        )
        if cover_match:
            cover = cover_match.group(1)
        note = ""
        note_match = re.search(r'<div class="module-item-note">(.*?)</div>', body, re.S)
        if note_match:
            note = strip_tags(note_match.group(1))
        name = attrs.get("title") or ""
        if not name:
            title_match = re.search(r'<div class="module-poster-item-title">(.*?)</div>', body, re.S)
            name = strip_tags(title_match.group(1)) if title_match else ""
        items.append(PosterItem(vod_id=vod.group(1), name=name.strip(), cover=cover, note=note))
    return items


def parse_home(html: str) -> HomeData:
    home = HomeData()
    titles = list(re.finditer(r'<h2 class="module-title">(.*?)</h2>', html, re.S))
    for index, match in enumerate(titles):
        start = match.end()
        end = titles[index + 1].start() if index + 1 < len(titles) else len(html)
        title = first_text(match.group(1))
        items = parse_poster_items(html[start:end])
        if not items:
            continue
        if not home.sections and not home.featured:
            home.featured = items[:8]
        home.sections.append((title or "推荐", items))
    return home


def parse_search(html: str) -> list[PosterItem]:
    """搜索结果卡片 module-card-item。"""
    items: list[PosterItem] = []
    chunks = re.split(r'<div class="module-card-item(?!-)[^"]*">', html)[1:]
    for chunk in chunks:
        anchor = re.search(r'<a href="(/vod/(\d+)/)"[^>]*class="module-card-item-poster"', chunk)
        if not anchor:
            continue
        vod_id = anchor.group(2)
        cover_match = re.search(r'data-original="([^"]+)"', chunk)
        note_match = re.search(r'<div class="module-item-note">(.*?)</div>', chunk, re.S)
        title_match = re.search(r'<div class="module-card-item-title">(.*?)</div>', chunk, re.S)
        class_match = re.search(r'<div class="module-card-item-class">(.*?)</div>', chunk, re.S)
        infos = re.findall(r'<div class="module-info-item-content">(.*?)</div>', chunk, re.S)
        year = region = ""
        genres: list[str] = []
        actors = ""
        if infos:
            meta = strip_tags(infos[0])
            parts = [p.strip() for p in meta.split("/") if p.strip()]
            for part in parts:
                if re.fullmatch(r"(19|20)\d{2}", part):
                    year = part
                elif not region and len(part) <= 8 and "动漫" not in part and "电影" not in part:
                    region = part
                else:
                    genres.extend(g.strip() for g in part.split(",") if g.strip())
        if len(infos) > 1:
            actors = strip_tags(infos[1])
        items.append(
            PosterItem(
                vod_id=vod_id,
                name=strip_tags(title_match.group(1)) if title_match else "",
                cover=cover_match.group(1) if cover_match else "",
                note=strip_tags(note_match.group(1)) if note_match else "",
                year=year,
                region=region,
                genres=genres,
                category=strip_tags(class_match.group(1)) if class_match else "",
                actors=actors,
            )
        )
    return items


def parse_listing(html: str) -> list[PosterItem]:
    return parse_poster_items(html)


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------


def parse_detail(html: str, vod_id: str) -> AnimeDetail:
    detail = AnimeDetail(vod_id=vod_id, name="")
    html = _COMMENT_RE.sub("", html)
    title_match = re.search(r"<h1>(.*?)</h1>", html, re.S)
    if title_match:
        detail.name = strip_tags(title_match.group(1))

    cover_match = re.search(r'<div class="module-item-pic"><img[^>]*data-original="([^"]+)"', html)
    if cover_match:
        detail.cover = cover_match.group(1)

    for tag_html in re.findall(r'<div class="module-info-tag-link">(.*?)</div>', html, re.S):
        text = strip_tags(tag_html)
        if re.fullmatch(r"(19|20)\d{2}", text):
            detail.year = text
        span = re.search(r'<span title="([^"]*)"', tag_html)
        if span and not detail.region:
            detail.region = html_lib.unescape(span.group(1))
        if "/vod-type/" in tag_html and not detail.category:
            detail.category = text

    info_rows = re.findall(
        r'<span class="module-info-item-title">(.*?)</span>\s*<div class="module-info-item-content">(.*?)</div>',
        html,
        re.S,
    )
    for label_html, value_html in info_rows:
        label = strip_tags(label_html).rstrip("：:")
        value = strip_tags(value_html)
        if label == "别名":
            detail.aliases = clean_list(value)
        elif label == "导演":
            detail.director = clean_list(value)
        elif label == "主演":
            detail.actors = clean_list(value)
        elif label.startswith("语言"):
            detail.language = value
        elif label == "更新":
            detail.updated = value
        elif label == "集数":
            detail.episode_note = value

    intro_match = re.search(r'<div class="module-info-introduction-content">(.*?)</div>', html, re.S)
    if intro_match:
        intro = strip_tags(intro_match.group(1))
        detail.intro = "" if intro in ("暂无简介", "暂无") else intro

    # 线路 Tab（隐藏的 mobile 面板是唯一带 data-sid 的地方，按出现顺序去重）
    tabs: list[tuple[int, str, int]] = []
    for sid_raw, show, _title, count_raw in re.findall(
        r'<div class="module-tab-item tab-item[^"]*"\s+data-sid="(\d+)"\s+data-show="([^"]*)"'
        r"\s*>\s*<span>(.*?)</span>\s*<small>(\d+)</small>",
        html,
        re.S,
    ):
        sid = int(sid_raw)
        if sid in [t[0] for t in tabs]:
            continue
        tabs.append((sid, html_lib.unescape(show), int(count_raw)))

    # 剧集：按 pane{sid} 面板解析，每个面板只含自己线路的分集
    panes: dict[int, list[Episode]] = {}
    marks = list(
        re.finditer(r'<div class="module-list sort-list tab-list play-tab-list[^"]*" id="pane(\d+)">', html)
    )
    for index, mark in enumerate(marks):
        sid = int(mark.group(1))
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(html)
        episodes: list[Episode] = []
        for anchor in _ANCHOR_RE.finditer(html[start:end]):
            attrs = parse_attrs(anchor.group(1))
            if "module-play-list-link" not in attrs.get("class", ""):
                continue
            hit = re.search(r"/vod-play/%s-(\d+)-(\d+)/?" % re.escape(vod_id), attrs.get("href", ""))
            if not hit:
                continue
            nid = int(hit.group(2))
            if any(ep.nid == nid for ep in episodes):
                continue
            label = first_text(anchor.group(2)) or "第%d集" % nid
            episodes.append(Episode(sid=sid, nid=nid, label=label, index=len(episodes) + 1))
        if episodes:
            panes[sid] = episodes

    known = [sid for sid, _, _ in tabs]
    for sid in panes:
        if sid not in known:
            known.append(sid)
    names = {sid: name for sid, name, _ in tabs}
    counts = {sid: count for sid, _, count in tabs}
    for sid in known:
        episodes = panes.get(sid)
        if not episodes:
            # 面板没有静态链接时，按 Tab 上标注的集数补出占位分集
            episodes = [
                Episode(sid=sid, nid=n + 1, label="第%d集" % (n + 1), index=n + 1)
                for n in range(counts.get(sid, 0))
            ]
        if not episodes:
            continue
        detail.sources.append(
            PlaySource(sid=sid, name=names.get(sid) or "线路 %d" % sid, episodes=episodes)
        )
    return detail


def parse_player_data(html: str) -> Optional[PlayInfo]:
    match = re.search(r"player_data\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    url = (data.get("url") or "").strip()
    if not url:
        return None
    return PlayInfo(
        url=url,
        next_url=(data.get("url_next") or "").strip(),
        prev_url=(data.get("link_pre") or "").strip(),
        flag=data.get("flag") or "",
        vod_id=str(data.get("id") or ""),
        sid=int(data.get("sid") or 1),
        nid=int(data.get("nid") or 1),
    )


# ---------------------------------------------------------------------------
# 站点 API
# ---------------------------------------------------------------------------

CATEGORIES: tuple[tuple[str, int], ...] = (
    ("中国动漫", 1),
    ("日本动漫", 2),
    ("欧美动漫", 3),
    ("里番动漫", 6),
    ("其他动漫", 4),
    ("动画电影", 5),
)


def _mask_item(item: PosterItem) -> PosterItem:
    """调试模式下把卡片名称改成别名、封面清空。"""
    if not masking.enabled() or masking.allowed(item.name):
        return item
    item.name = masking.name(item.name)
    item.cover = masking.cover(item.cover, item.name)
    item.actors = masking.hidden(item.actors, "")
    return item


def _mask_items(items: list[PosterItem]) -> list[PosterItem]:
    return [_mask_item(item) for item in items]


def _mask_detail(detail: AnimeDetail) -> AnimeDetail:
    if not masking.enabled() or masking.allowed(detail.name):
        return detail
    detail.name = masking.name(detail.name)
    detail.cover = ""
    detail.aliases = masking.hidden(detail.aliases, "")
    detail.director = masking.hidden(detail.director, "")
    detail.actors = masking.hidden(detail.actors, "")
    detail.intro = masking.hidden(detail.intro)
    return detail


def _mask_home(home: HomeData) -> HomeData:
    home.featured = _mask_items(home.featured)
    home.sections = [(title, _mask_items(items)) for title, items in home.sections]
    return home


class SiteApi:
    """对外统一入口；镜像切换时可安全替换 base。"""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._base = ""
        self._lock = threading.RLock()
        self.on_failover: Optional[Callable[[], None]] = None

    # ---------- 镜像 ----------
    def set_base(self, base: str) -> None:
        with self._lock:
            self._base = base.rstrip("/")

    @property
    def base(self) -> str:
        with self._lock:
            return self._base

    def _get(self, path: str, referer: Optional[str] = None) -> str:
        last_error: Exception = NetworkError("尚未选择可用镜像")
        for attempt in range(2):
            base = self.base
            if not base:
                if attempt == 0 and self.on_failover:
                    self.on_failover()
                    continue
                raise last_error
            try:
                return self.client.get_text(base + path, referer=referer)
            except NetworkError as exc:
                last_error = exc
                if attempt == 0 and self.on_failover:
                    self.on_failover()
                    continue
                raise
        raise last_error

    # ---------- 数据 ----------
    def home(self) -> HomeData:
        return _mask_home(parse_home(self._get("/")))

    def search(self, keyword: str, page: int = 1) -> list[PosterItem]:
        keyword = keyword.strip()
        if not keyword:
            return []
        if page <= 1:
            path = "/vod-search/-------------/?wd=" + quote(keyword)
        else:
            path = "/vod-search/%s----------%d---/" % (quote(keyword), page)
        items = parse_search(self._get(path))
        # 调试模式下可以放行搜索结果：要排查的条目本来就是靠搜索找出来的
        if masking.enabled() and masking.keep_search():
            return items
        return _mask_items(items)

    def listing(self, type_id: int, page: int = 1) -> list[PosterItem]:
        if page <= 1:
            path = "/vod-show/%d-----------/" % type_id
        else:
            path = "/vod-show/%d--------%d---/" % (type_id, page)
        return _mask_items(parse_listing(self._get(path)))

    def detail(self, vod_id: str) -> AnimeDetail:
        return _mask_detail(parse_detail(self._get("/vod/%s/" % vod_id), vod_id))

    def play_info(self, vod_id: str, sid: int, nid: int) -> PlayInfo:
        path = "/vod-play/%s-%d-%d/?iframe=1" % (vod_id, sid, nid)
        html = self._get(path, referer=self.base + "/vod/%s/" % vod_id)
        info = parse_player_data(html)
        if info is None:
            raise NetworkError("播放地址解析失败，可能该线路已失效")
        return info
