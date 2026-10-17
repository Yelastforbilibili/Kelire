"""应用级共享上下文：把各页面都要用的对象集中在一处。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import Settings, Store
from ..core.downloader import DownloadManager
from ..core.http import HttpClient
from ..core.mirrors import MirrorReport
from ..core.proxy import HlsProxy
from ..core.site import SiteApi
from .imagecache import ImageCache
from .workers import TaskRunner


@dataclass
class AppContext:
    store: Store
    settings: Settings
    api: SiteApi
    client: HttpClient
    proxy: HlsProxy
    images: ImageCache
    tasks: TaskRunner
    downloads: DownloadManager
    toast: object = None  # components.Toast，窗口创建后注入
    mirror: Optional[MirrorReport] = None

    def save(self) -> None:
        self.store.settings = self.settings
        self.store.save()

    def download_root(self) -> Path:
        return self.settings.download_root()

    def notify(self, text: str, icon_name: str = "check", color: str = "") -> None:
        toast = self.toast
        if toast is None:
            return
        if color:
            toast.show_message(text, icon_name, color)
        else:
            toast.show_message(text, icon_name)
