"""封面图异步加载：内存 LRU + 磁盘缓存 + 线程池抓取。

工作线程只负责取字节并解码成 QImage（QImage 可在非 GUI 线程创建），
位图转 QPixmap 留给界面线程。
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from ..core.http import MediaClient

_MEMORY_LIMIT = 480


def _digest(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()


def _emit(signal, *args) -> None:
    """窗口关闭后工作线程可能仍在收尾，信号对象已被回收时直接丢弃。"""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class _Fetch(QRunnable):
    def __init__(self, owner: "ImageCache", url: str) -> None:
        super().__init__()
        self.owner = owner
        self.url = url
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - QRunnable 接口
        owner = self.owner
        if owner._closed:
            return
        path = owner._disk_path(self.url)
        data: Optional[bytes] = None
        if path.exists():
            try:
                data = path.read_bytes()
            except OSError:
                data = None
        if not data:
            try:
                data = owner.client.get_bytes(self.url, timeout=20, retries=1)
            except Exception:  # noqa: BLE001 - 封面失败不打扰用户
                data = None
            if data:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                except OSError:
                    pass
        if not data:
            _emit(owner._failed, self.url)
            return
        image = QImage()
        if not image.loadFromData(data) or image.isNull():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            _emit(owner._failed, self.url)
            return
        _emit(owner._ready, self.url, image)


class ImageCache(QObject):
    ready = Signal(str, QImage)
    _ready = Signal(str, QImage)
    _failed = Signal(str)

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = MediaClient()
        self.root = Path(root)
        self._memory: "OrderedDict[str, QImage]" = OrderedDict()
        self._pending: set[str] = set()
        self._failed_urls: set[str] = set()
        self._lock = threading.Lock()
        self._watchers: dict[str, list[Callable[[str, QImage], None]]] = {}
        self._closed = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(12)
        self._ready.connect(self._store)
        self._failed.connect(self._mark_failed)

    # ---------- 缓存 ----------
    def _disk_path(self, url: str) -> Path:
        key = _digest(url)
        return self.root / key[:2] / (key + ".img")

    def _store(self, url: str, image: QImage) -> None:
        with self._lock:
            self._memory[url] = image
            self._memory.move_to_end(url)
            while len(self._memory) > _MEMORY_LIMIT:
                self._memory.popitem(last=False)
            self._pending.discard(url)
            callbacks = self._watchers.pop(url, [])
        for callback in callbacks:
            try:
                callback(url, image)
            except RuntimeError:
                pass
        try:
            self.ready.emit(url, image)
        except RuntimeError:
            pass

    def _mark_failed(self, url: str) -> None:
        with self._lock:
            self._pending.discard(url)
            self._failed_urls.add(url)

    def cached(self, url: str) -> Optional[QImage]:
        if not url:
            return None
        with self._lock:
            image = self._memory.get(url)
            if image is not None:
                self._memory.move_to_end(url)
            return image

    def subscribe(self, url: str, callback: Callable[[str, QImage], None]) -> None:
        """图片就绪后回调一次；已缓存则立即回调。"""
        if not url or self._closed:
            return
        image = self.cached(url)
        if image is not None:
            callback(url, image)
            return
        with self._lock:
            self._watchers.setdefault(url, []).append(callback)

    # ---------- 请求 ----------
    def request(self, url: str) -> Optional[QImage]:
        """返回已缓存的图；未缓存时排队抓取并返回 None。"""
        if not url:
            return None
        image = self.cached(url)
        if image is not None:
            return image
        with self._lock:
            if url in self._pending or url in self._failed_urls:
                return None
            self._pending.add(url)
        self._pool.start(_Fetch(self, url))
        return None

    def close(self) -> None:
        self._closed = True
        self._pool.clear()
        self._pool.waitForDone(1500)
