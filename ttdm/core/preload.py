"""剧集预加载：把指定集的前 N% 分片提前拉进播放缓存。

播放器打开某一集时，本地代理会按分片签名复用磁盘缓存，所以只要把这些分片
提前解密落盘，真正切到那一集时就是「打开即播」。同一时间只预加载一集
（按剧集顺序排队），单集内部仍然是并发拉取，但只用几个连接，避免和正在
播放的那一集抢带宽。
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .hls import KeyCache, Segment, decrypt_aes128, playlist_signature, resolve_playlist
from .http import MediaClient
from .site import SiteApi

log = logging.getLogger(__name__)

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_IDLE = "idle"

# 预加载最多同时用几个连接：多了会明显拖慢正在播放的那一集
PRELOAD_WORKERS = 4


class _Aborted(Exception):
    """本次预加载被取消（用户取消、切集或关闭播放页）。"""


@dataclass
class PreloadUpdate:
    nid: int
    state: str
    percent: float = 0.0
    error: str = ""

    @property
    def selected(self) -> bool:
        return self.state in (STATE_QUEUED, STATE_RUNNING, STATE_DONE, STATE_ERROR)


@dataclass
class _Task:
    order: int
    vod_id: str
    sid: int
    nid: int
    label: str
    percent: int


class PreloadManager(QObject):
    """一个后台线程按顺序处理预加载任务，进度用 Qt 信号回主线程。"""

    update = Signal(object)

    def __init__(
        self,
        api: SiteApi,
        cache_root: Optional[Path],
        parent: Optional[QObject] = None,
        workers: int = PRELOAD_WORKERS,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._cache_root = cache_root
        self._workers = max(1, min(8, workers))
        self._client = MediaClient()
        self._key_cache = KeyCache(self._client)
        self._cond = threading.Condition(threading.RLock())
        self._tasks: list[_Task] = []
        self._states: dict[int, PreloadUpdate] = {}
        self._current: Optional[_Task] = None
        self._vod_id = ""
        self._sid = 0
        self._playing = -1
        self._token = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="preload", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- 上下文
    def set_context(self, vod_id: str, sid: int) -> None:
        """换了作品或线路：之前的预加载全部作废。"""
        with self._cond:
            if vod_id == self._vod_id and sid == self._sid:
                return
            self._vod_id = vod_id
            self._sid = sid
            self._cancel_locked()
            self._states.clear()

    def set_playing(self, nid: int) -> None:
        """正在播放的这一集不能预加载：取消它的任务并拒绝再次选中。"""
        with self._cond:
            self._playing = int(nid)
            task = next((item for item in self._tasks if item.nid == self._playing), None)
            if task is None:
                return
            self._tasks.remove(task)
            if self._current is not None and self._current.nid == self._playing:
                self._token += 1
            self._states.pop(self._playing, None)
            self._emit(self._playing, STATE_IDLE, 0.0)
            self._cond.notify_all()

    # ---------------------------------------------------------------- 选择
    def toggle(
        self,
        nid: int,
        label: str,
        order: int,
        max_count: int,
        percent: int,
    ) -> str:
        """返回 added / removed / refused / busy / unavailable。"""
        if self._cache_root is None:
            return "unavailable"
        with self._cond:
            if nid == self._playing:
                return "busy"
            task = next((item for item in self._tasks if item.nid == nid), None)
            if task is not None:
                self._tasks.remove(task)
                if self._current is not None and self._current.nid == nid:
                    self._token += 1
                self._states.pop(nid, None)
                self._emit(nid, STATE_IDLE, 0.0)
                self._cond.notify_all()
                return "removed"
            if self.count() >= max(1, max_count):
                return "refused"
            self._tasks.append(
                _Task(
                    order=order,
                    vod_id=self._vod_id,
                    sid=self._sid,
                    nid=nid,
                    label=label,
                    percent=max(1, min(80, int(percent))),
                )
            )
            self._tasks.sort(key=lambda item: item.order)
            self._emit(nid, STATE_QUEUED, 0.0)
            self._cond.notify_all()
            return "added"

    def states(self) -> dict[int, PreloadUpdate]:
        with self._cond:
            return dict(self._states)

    def selected(self) -> list[int]:
        with self._cond:
            return [task.nid for task in self._tasks]

    def count(self) -> int:
        """已经占着名额的集数：排队/进行中的，加上失败后还留在列表里的。"""
        with self._cond:
            queued = {task.nid for task in self._tasks}
            extra = sum(
                1
                for nid, update in self._states.items()
                if update.state == STATE_ERROR and nid not in queued
            )
            return len(self._tasks) + extra

    def cancel_all(self) -> None:
        with self._cond:
            self._cancel_locked()
            self._states.clear()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cancel_locked()
            self._cond.notify_all()

    # ---------------------------------------------------------------- 内部
    def _cancel_locked(self) -> None:
        self._token += 1
        self._tasks.clear()
        self._cond.notify_all()

    def _emit(self, nid: int, state: str, percent: float, error: str = "") -> None:
        update = PreloadUpdate(nid=nid, state=state, percent=max(0.0, min(1.0, percent)), error=error)
        self._states[nid] = update
        self.update.emit(update)

    def _check(self, token: int) -> None:
        with self._cond:
            if self._closed or token != self._token:
                raise _Aborted()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._tasks and not self._closed:
                    self._cond.wait(0.5)
                if self._closed:
                    return
                task = self._tasks[0]
                token = self._token
                self._current = task
            self._emit(task.nid, STATE_RUNNING, 0.0)
            error = ""
            try:
                self._preload(task, token)
            except _Aborted:
                pass
            except Exception as exc:  # noqa: BLE001 - 单集失败不影响队列里后面的
                error = str(exc) or exc.__class__.__name__
                log.info("预加载失败 %s: %s", task.label, error)
            with self._cond:
                if self._current is not None and self._current.nid == task.nid:
                    self._current = None
                current = next((item for item in self._tasks if item.nid == task.nid), None)
                if current is not None:
                    self._tasks.remove(current)
                aborted = token != self._token or self._closed
            if aborted:
                self._states.pop(task.nid, None)
                continue
            if error:
                percent = self._states.get(task.nid, PreloadUpdate(task.nid, STATE_ERROR, 0.0)).percent
                self._emit(task.nid, STATE_ERROR, percent, error)
            else:
                self._emit(task.nid, STATE_DONE, 1.0)

    def _preload(self, task: _Task, token: int) -> None:
        """解析清单 -> 只取前 percent% 的分片 -> 解密写进播放缓存目录。"""
        info = self._api.play_info(task.vod_id, task.sid, task.nid)
        referer = "%s/vod/%s/" % (self._api.base, task.vod_id)
        playlist = resolve_playlist(self._client, info.url, referer=referer)
        segments: list[Segment] = playlist.segments
        if not segments:
            raise RuntimeError("播放列表为空")
        self._check(token)
        total = max(1, int(round(len(segments) * task.percent / 100.0)))
        wanted = segments[:total]
        cache_dir: Optional[Path] = None
        if self._cache_root is not None:
            cache_dir = self._cache_root / playlist_signature(segments)
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                cache_dir = None
        pending: list[Segment] = []
        for segment in wanted:
            if cache_dir is not None and (cache_dir / ("seg_%05d.ts" % segment.index)).exists():
                continue
            pending.append(segment)
        done = len(wanted) - len(pending)
        log.info(
            "预加载 %s：共 %d 段，目标前 %d 段，待下载 %d 段",
            task.label,
            len(segments),
            len(wanted),
            len(pending),
        )
        self._emit(task.nid, STATE_RUNNING, done / len(wanted) if wanted else 1.0)
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="preload-seg") as pool:
            futures = [pool.submit(self._fetch_one, segment, cache_dir, token) for segment in pending]
            try:
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    self._check(token)
                    self._emit(task.nid, STATE_RUNNING, done / max(1, len(wanted)))
                log.info("预加载 %s 完成：%d/%d 段", task.label, done, len(wanted))
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    def _fetch_one(self, segment: Segment, cache_dir: Optional[Path], token: int) -> None:
        self._check(token)
        raw = self._client.get_bytes(segment.url, timeout=30, retries=1)
        if segment.key_url:
            key = self._key_cache.key(segment.key_url)
            raw = decrypt_aes128(raw, key, segment.iv)
        self._check(token)
        if cache_dir is None:
            return
        path = cache_dir / ("seg_%05d.ts" % segment.index)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
        except OSError:
            pass
