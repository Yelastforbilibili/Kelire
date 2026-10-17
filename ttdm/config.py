"""配置与本地数据持久化（设置、观看记录、下载记录、镜像缓存）。"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from .paths import data_dir, default_download_dir, settings_file

# 内置语言（语言文件在 ttdm/locales/<代码>.json，新增语言不需要改这里）
DEFAULT_LANGUAGE = "zh_CN"


@dataclass
class Settings:
    download_dir: str = ""
    download_workers: int = 24          # yt-dlp 分片并发
    download_parallel: int = 2          # 同时下载的剧集数
    buffer_workers: int = 24            # 在线缓冲并发
    mirror_override: str = ""
    refresh_minutes: int = 5
    remember_position: bool = True
    auto_next: bool = True
    volume: int = 85
    speed: float = 1.0
    cache_limit_mb: int = 8192
    reduce_motion: bool = False
    last_page: str = "home"
    theme: str = "dark"
    accent_dark: str = ""                     # 深色主题的自定义强调色（空＝默认）
    accent_light: str = ""                    # 浅色主题的自定义强调色
    language: str = DEFAULT_LANGUAGE          # 界面语言，语言文件在 ttdm/locales
    debug_blank: bool = False                 # 调试模式：隐藏名称与封面
    debug_keep_search: bool = True            # 调试模式下搜索结果保持正常显示
    cache_categories: bool = True             # 分类片库首次访问后缓存
    onboarded: bool = False                   # 是否已通过免责声明与新手指引
    preload_count: int = 3                    # 播放页最多预加载几集
    preload_percent: int = 10                 # 每集只预加载前百分之几
    filename_template: str = "{集数}"    # 下载文件名模板
    categories: list = field(default_factory=lambda: [1, 2, 3, 6, 4, 5])
    resume_on_open: bool = False        # 打开详情是否直接续播（默认进详情页）

    def download_root(self) -> Path:
        if self.download_dir:
            return Path(self.download_dir)
        return default_download_dir()

    def normalized(self) -> "Settings":
        self.download_workers = max(1, min(64, int(self.download_workers)))
        self.download_parallel = max(1, min(6, int(self.download_parallel)))
        self.buffer_workers = max(4, min(64, int(self.buffer_workers)))
        self.refresh_minutes = max(1, min(60, int(self.refresh_minutes)))
        self.volume = max(0, min(100, int(self.volume)))
        self.speed = min(3.0, max(0.5, float(self.speed)))
        self.cache_limit_mb = max(512, int(self.cache_limit_mb))
        self.preload_count = max(1, min(6, int(self.preload_count)))
        self.preload_percent = max(5, min(50, int(self.preload_percent)))
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        # 语言代码不锁死：往 ttdm/locales 里丢一个新 json 就能多一种语言，
        # 真正不认识的语言由 i18n.set_language 回退到中文
        if not isinstance(self.language, str) or not self.language.strip():
            self.language = "zh_CN"
        self.filename_template = (self.filename_template or "{集数}").strip() or "{集数}"
        known = {1, 2, 3, 6, 4, 5}
        try:
            picked = [int(item) for item in self.categories]
        except (TypeError, ValueError):
            picked = []
        self.categories = [item for item in [1, 2, 3, 6, 4, 5] if item in set(picked) & known]
        if not self.categories:
            self.categories = [1, 2, 3, 6, 4, 5]
        return self


@dataclass
class HistoryEntry:
    vod_id: str
    name: str
    cover: str = ""
    sid: int = 1
    source_name: str = ""
    nid: int = 1
    episode: str = ""
    position: float = 0.0
    duration: float = 0.0
    total_episodes: int = 0
    updated_at: float = 0.0


@dataclass
class DownloadRecord:
    task_id: str
    vod_id: str
    name: str
    cover: str = ""
    episode: str = ""
    source_name: str = ""
    path: str = ""
    size: int = 0
    status: str = "done"
    finished_at: float = 0.0


class Store:
    """settings.json 读写，全部操作加锁。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else settings_file()
        self._lock = threading.RLock()
        self.settings = Settings()
        self.history: list[HistoryEntry] = []
        self.downloads: list[DownloadRecord] = []
        self.mirrors: list[str] = []
        self.last_mirror: str = ""
        self.load()

    # ---------- 读写 ----------
    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if isinstance(raw.get("settings"), dict):
                data = raw["settings"]
                known = {f.name for f in fields(Settings)}
                self.settings = Settings(**{k: v for k, v in data.items() if k in known})
            self.history = self._load_list(raw.get("history"), HistoryEntry)
            self.downloads = self._load_list(raw.get("downloads"), DownloadRecord)
            self.mirrors = [m for m in raw.get("mirrors", []) if isinstance(m, str)]
            self.last_mirror = raw.get("last_mirror", "") if isinstance(raw.get("last_mirror"), str) else ""
            self.settings.normalized()

    @staticmethod
    def _load_list(items: Any, cls: type) -> list:
        out = []
        known = {f.name for f in fields(cls)}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                out.append(cls(**{k: v for k, v in item.items() if k in known}))
            except TypeError:
                continue
        return out

    def save(self) -> None:
        with self._lock:
            payload = {
                "settings": asdict(self.settings),
                "history": [asdict(h) for h in self.history[:80]],
                "downloads": [asdict(d) for d in self.downloads[:200]],
                "mirrors": self.mirrors[:24],
                "last_mirror": self.last_mirror,
            }
            tmp = self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass

    # ---------- 观看记录 ----------
    def touch_history(
        self,
        vod_id: str,
        name: str,
        cover: str,
        sid: int,
        source_name: str,
        nid: int,
        episode: str,
        position: float,
        duration: float,
        total_episodes: int,
    ) -> None:
        with self._lock:
            entry = next((h for h in self.history if h.vod_id == vod_id), None)
            if entry is None:
                entry = HistoryEntry(vod_id=vod_id, name=name)
                self.history.insert(0, entry)
            entry.name = name
            if cover:
                entry.cover = cover
            entry.sid = sid
            entry.source_name = source_name
            entry.nid = nid
            entry.episode = episode
            entry.position = max(0.0, float(position))
            entry.duration = max(0.0, float(duration))
            entry.total_episodes = total_episodes
            entry.updated_at = time.time()
            self.history.sort(key=lambda h: h.updated_at, reverse=True)
            del self.history[80:]

    def get_history(self, vod_id: str) -> Optional[HistoryEntry]:
        with self._lock:
            return next((h for h in self.history if h.vod_id == vod_id), None)

    def drop_history(self, vod_id: str) -> None:
        with self._lock:
            self.history = [h for h in self.history if h.vod_id != vod_id]

    def clear_history(self) -> None:
        with self._lock:
            self.history = []

    # ---------- 下载记录 ----------
    def add_download(self, record: DownloadRecord) -> None:
        with self._lock:
            self.downloads = [d for d in self.downloads if d.task_id != record.task_id]
            self.downloads.insert(0, record)
            del self.downloads[200:]

    def clear_downloads(self) -> None:
        with self._lock:
            self.downloads = []

    def remove_download(self, task_id: str) -> None:
        with self._lock:
            self.downloads = [d for d in self.downloads if d.task_id != task_id]

    def reset_settings(self) -> Settings:
        with self._lock:
            self.settings = Settings().normalized()
            return self.settings

    def reset_all(self) -> Settings:
        """清空所有本地数据，用来兜住「配置写坏了导致卡 bug」的情况。"""
        with self._lock:
            self.settings = Settings().normalized()
            self.history = []
            self.downloads = []
            self.mirrors = []
            self.last_mirror = ""
            return self.settings

    def has_download(self, vod_id: str, episode: str) -> bool:
        with self._lock:
            return any(
                d.vod_id == vod_id and d.episode == episode and d.status == "done"
                for d in self.downloads
            )

    # ---------- 镜像 ----------
    def set_mirrors(self, mirrors: list[str], current: str) -> None:
        with self._lock:
            self.mirrors = list(mirrors)
            self.last_mirror = current
