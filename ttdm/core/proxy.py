"""本地 HLS 代理：多线程拉取并解密分片，供播放器顺畅读取。

原站在线播放是一块一块顺序拉取、分片还带 AES-128 加密，拖动进度条很卡。
这里把清单改写成指向本机 127.0.0.1 的 VOD 清单：本地并发预取 + 内存/磁盘
缓存，播放器（QMediaPlayer/ffmpeg）只读本机，起播快、拖动也快。
"""

from __future__ import annotations

import heapq
import logging
import math
import os
import shutil
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .hls import KeyCache, Segment, decrypt_aes128, playlist_signature
from .http import MediaClient

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"


class _MemoryCache:
    """按分片序号缓存，受字节上限约束。"""

    def __init__(self, limit_bytes: int) -> None:
        self._limit = max(16 * 1024 * 1024, limit_bytes)
        self._size = 0
        self._items: OrderedDict[int, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: int) -> Optional[bytes]:
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)
            return data

    def put(self, key: int, data: bytes) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._size -= len(old)
            self._items[key] = data
            self._size += len(data)
            while self._size > self._limit and self._items:
                _, dropped = self._items.popitem(last=False)
                self._size -= len(dropped)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


class _Canceled(Exception):
    """分片任务被 seek 取消。"""


class _Job:
    """一个待取分片：玩家点名的优先，预取的可被 seek 丢掉。"""

    __slots__ = ("index", "event", "data", "error", "started", "pinned")

    def __init__(self, index: int, pinned: bool = False) -> None:
        self.index = index
        self.event = threading.Event()
        self.data: Optional[bytes] = None
        self.error: Optional[BaseException] = None
        self.started = False
        self.pinned = pinned


class HlsSession:
    """一次播放会话：一份已解密的媒体清单 + 按优先级调度的并发抓取器。

    播放器每要一段就调用 get()，这一段会被提到队首；预取只排在它后面，
    并且拖动进度条后，旧位置那些还没开始的预取会被直接丢掉，不再和新的
    位置抢连接——之前 seek 到后面很慢，就是因为预取任务排在请求前面。
    """

    def __init__(
        self,
        session_id: str,
        client: MediaClient,
        key_cache: KeyCache,
        segments: list[Segment],
        referer: str = "",
        cache_dir: Optional[Path] = None,
        workers: int = 16,
        memory_limit: int = 128 * 1024 * 1024,
    ) -> None:
        self.id = session_id
        self.client = client
        self.key_cache = key_cache
        self.segments = segments
        self.referer = referer
        self.workers = max(4, workers)
        self.cache = _MemoryCache(memory_limit)
        self.cache_dir = cache_dir
        self.requests = 0
        self.bytes_served = 0
        self._cond = threading.Condition(threading.Lock())
        self._jobs: dict[int, _Job] = {}
        self._heap: list[tuple[int, int]] = []
        self._playhead = 0
        self._closed = threading.Event()
        self._cap = max(12, self.workers * 2)
        self._ready: set[int] = set()
        if self.cache_dir is not None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.cache_dir = None
        if self.cache_dir is not None:
            for segment in self.segments:
                path = self.segment_file(segment.index)
                if path is not None and path.exists():
                    self._ready.add(segment.index)
        self._threads = [
            threading.Thread(target=self._worker, name="hls-fetch-%d" % index, daemon=True)
            for index in range(self.workers)
        ]
        for thread in self._threads:
            thread.start()

    # ---------- 信息 ----------
    @property
    def duration(self) -> float:
        return sum(seg.duration for seg in self.segments)

    def segment_file(self, index: int) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        return self.cache_dir / ("seg_%05d.ts" % index)

    def _set_playhead(self, index: int) -> None:
        self._playhead = max(0, min(len(self.segments) - 1, index))

    # ---------- 队列 ----------
    def _priority(self, index: int) -> int:
        distance = index - self._playhead
        if distance == 0:
            return 0
        if distance < 0:
            return 80 + min(60, -distance)
        return 1 + min(60, distance // 3)

    def _queue_locked(
        self, index: int, priority: Optional[int] = None, pinned: bool = False
    ) -> Optional[_Job]:
        if index < 0 or index >= len(self.segments) or self._closed.is_set():
            return None
        job = self._jobs.get(index)
        if job is not None:
            if pinned:
                job.pinned = True
            return job
        if self.cache.get(index) is not None:
            return None
        path = self.segment_file(index)
        if path is not None and path.exists():
            return None
        if not pinned and len(self._jobs) >= self._cap:
            return None
        job = self._jobs[index] = _Job(index, pinned=pinned)
        heapq.heappush(self._heap, (self._priority(index) if priority is None else priority, index))
        self._cond.notify()
        return job

    def _drop_stale_locked(self) -> None:
        """丢掉离播放位置太远的预取（没开始跑的），让带宽集中在新位置。"""
        low = self._playhead - 1
        high = self._playhead + self.workers * 4
        dropped: list[int] = []
        for index, job in list(self._jobs.items()):
            if job.started or job.pinned:
                continue
            if index < low or index > high:
                dropped.append(index)
        for index in dropped:
            job = self._jobs.pop(index, None)
            if job is None:
                continue
            job.error = _Canceled("分片任务已取消")
            job.event.set()
        if dropped:
            self._heap = [entry for entry in self._heap if entry[1] in self._jobs]
            heapq.heapify(self._heap)

    # ---------- 取片 ----------
    def get(self, index: int, timeout: float = 60.0) -> bytes:
        """给播放器取一段。慢或抖动时多等几轮，尽量不返回错误让播放器从头重来。"""
        if index < 0 or index >= len(self.segments):
            raise IndexError(index)
        data = self.cache.get(index)
        if data is not None:
            self._ready.add(index)
            self._set_playhead(index)
            return data
        path = self.segment_file(index)
        if path is not None and path.exists():
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if data:
                self.cache.put(index, data)
                self._ready.add(index)
                self._set_playhead(index)
                self.prefetch(index + 1)
                return data
        with self._cond:
            self._set_playhead(index)
            self._drop_stale_locked()
            job = self._queue_locked(index, priority=0, pinned=True)
            self._cond.notify_all()
        if job is None:
            data = self.cache.get(index)
            if data is not None:
                return data
            raise RuntimeError("分片不可用")
        deadline = time.monotonic() + max(timeout, 30.0) * 3
        while True:
            wait_for = max(5.0, min(60.0, deadline - time.monotonic()))
            if job.event.wait(wait_for):
                break
            if self._closed.is_set():
                raise RuntimeError("会话已关闭")
            if time.monotonic() >= deadline:
                raise TimeoutError("分片下载超时")
            # 还是没到：可能 worker 被别的任务占了，重新排到队首再等一次
            with self._cond:
                self._set_playhead(index)
                self._queue_locked(index, priority=0, pinned=True)
                self._cond.notify_all()
        if job.error is not None:
            raise job.error
        self.prefetch(index + 1)
        return job.data or b""

    def prefetch(self, start: int, ahead: Optional[int] = None) -> None:
        ahead = ahead if ahead is not None else self.workers
        with self._cond:
            for index in range(max(0, start), min(len(self.segments), start + ahead)):
                if self._closed.is_set():
                    return
                self._queue_locked(index)

    def bump(self, index: int, ahead: int = 0) -> None:
        """拖动进度条：目标段提到队首，后面的按距离排，远处的预取作废。"""
        with self._cond:
            self._set_playhead(index)
            self._drop_stale_locked()
            self._queue_locked(self._playhead, priority=0, pinned=True)
            for offset in range(1, max(1, ahead) + 1):
                self._queue_locked(self._playhead + offset, priority=1 + offset)
            self._cond.notify_all()

    def _download(self, index: int) -> bytes:
        cached = self.cache.get(index)
        if cached is not None:
            return cached
        seg = self.segments[index]
        last_error: Optional[Exception] = None
        for attempt in range(3):
            if self._closed.is_set():
                raise RuntimeError("会话已关闭")
            try:
                raw = self.client.get_bytes(seg.url, timeout=45, retries=2)
                break
            except Exception as exc:  # noqa: BLE001 - 网络抖动重试
                last_error = exc
                self._closed.wait(0.4 * (attempt + 1))
        else:
            raise last_error or RuntimeError("分片下载失败")
        if seg.key_url:
            key = self.key_cache.key(seg.key_url)
            raw = decrypt_aes128(raw, key, seg.iv)
        self.cache.put(index, raw)
        self._ready.add(index)
        path = self.segment_file(index)
        if path is not None:
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, path)
            except OSError:
                pass
        return raw

    def _worker(self) -> None:
        while not self._closed.is_set():
            job: Optional[_Job] = None
            with self._cond:
                while not self._heap and not self._closed.is_set():
                    self._cond.wait(0.4)
                if self._closed.is_set():
                    return
                while self._heap:
                    _, index = heapq.heappop(self._heap)
                    candidate = self._jobs.get(index)
                    if candidate is not None and not candidate.started:
                        candidate.started = True
                        job = candidate
                        break
            if job is None:
                continue
            try:
                data = self._download(job.index)
            except Exception as exc:  # noqa: BLE001 - 失败要让等待方立即知道，且可重试
                with self._cond:
                    self._jobs.pop(job.index, None)
                job.error = exc
                job.event.set()
                continue
            with self._cond:
                self._jobs.pop(job.index, None)
            job.data = data
            job.event.set()

    # ---------- 清单 ----------
    def buffer_ranges(self) -> list[tuple[float, float]]:
        """已经就绪的时间区间（秒），给播放器的缓冲条用。"""
        ranges: list[tuple[float, float]] = []
        start = 0.0
        current: Optional[float] = None
        for segment in self.segments:
            if segment.index in self._ready or self.cache.get(segment.index) is not None:
                if current is None:
                    current = start
                end = start + (segment.duration or 0.0)
            else:
                if current is not None:
                    ranges.append((current, start))
                    current = None
                end = start + (segment.duration or 0.0)
            start = end
        if current is not None:
            ranges.append((current, start))
        return ranges

    def stats(self) -> tuple[int, int]:
        """已缓存（内存或磁盘）的分片数与总分片数。"""
        cached = 0
        for index in range(len(self.segments)):
            if self.cache.get(index) is not None:
                cached += 1
                continue
            path = self.segment_file(index)
            if path is not None and path.exists():
                cached += 1
        return cached, len(self.segments)

    def local_playlist(self, base_url: str) -> str:
        target = max(1, int(math.ceil(max((s.duration for s in self.segments), default=1))))
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-TARGETDURATION:%d" % target,
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-INDEPENDENT-SEGMENTS",
        ]
        for seg in self.segments:
            lines.append("#EXTINF:%.3f," % (seg.duration or 1.0))
            lines.append("%s/hls/%s/%05d.ts" % (base_url, self.id, seg.index))
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def close(self) -> None:
        self._closed.set()
        with self._cond:
            for job in list(self._jobs.values()):
                if not job.started:
                    job.error = RuntimeError("会话已关闭")
                    job.event.set()
            self._jobs.clear()
            self._heap.clear()
            self._cond.notify_all()
        self.cache.clear()


class HlsProxy:
    """后台 HTTP 服务；所有会话共享一个端口。"""

    def __init__(self, cache_root: Optional[Path] = None, cache_limit_mb: int = 8192) -> None:
        self.cache_root = cache_root
        self.cache_limit = max(512, cache_limit_mb) * 1024 * 1024
        self.client = MediaClient()
        self.key_cache = KeyCache(self.client)
        self._sessions: dict[str, HlsSession] = {}
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    # ---------- 生命周期 ----------
    def start(self) -> str:
        with self._lock:
            if self._server is not None:
                return self.base_url
            proxy = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, fmt, *args):  # noqa: A003 - 静音访问日志
                    return

                def do_GET(self):  # noqa: N802
                    proxy._handle(self)

                def do_HEAD(self):  # noqa: N802
                    proxy._handle(self, head_only=True)

            try:
                server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
            except OSError:
                server = ThreadingHTTPServer(("localhost", 0), Handler)
            server.daemon_threads = True
            self._server = server
            self.port = server.server_address[1]
            self._thread = threading.Thread(target=server.serve_forever, name="hls-proxy", daemon=True)
            self._thread.start()
            self._prune()
        log.info("HLS 代理已启动 %s", self.base_url)
        return self.base_url

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (LOOPBACK, self.port)

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            server, self._server = self._server, None
        for session in sessions:
            session.close()
        if server is not None:
            server.shutdown()
            server.server_close()

    # ---------- 会话 ----------
    def open(
        self,
        segments: list[Segment],
        referer: str = "",
        workers: int = 16,
        memory_limit: int = 128 * 1024 * 1024,
    ) -> tuple[HlsSession, str]:
        self.start()
        session_id = uuid.uuid4().hex[:12]
        cache_dir: Optional[Path] = None
        if self.cache_root is not None:
            cache_dir = self.cache_root / playlist_signature(segments)
        session = HlsSession(
            session_id=session_id,
            client=self.client,
            key_cache=self.key_cache,
            segments=segments,
            referer=referer,
            cache_dir=cache_dir,
            workers=workers,
            memory_limit=memory_limit,
        )
        with self._lock:
            self._sessions[session_id] = session
        session.prefetch(0, ahead=max(6, workers))
        return session, "%s/hls/%s/index.m3u8" % (self.base_url, session_id)

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    # ---------- 请求 ----------
    def _handle(self, handler: BaseHTTPRequestHandler, head_only: bool = False) -> None:
        path = handler.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        try:
            if len(parts) == 3 and parts[0] == "hls" and parts[2] == "index.m3u8":
                session = self._session(parts[1])
                if session is None:
                    self._send(handler, 404, b"session not found", "text/plain", head_only)
                    return
                body = session.local_playlist(self.base_url).encode("utf-8")
                self._send(handler, 200, body, "application/vnd.apple.mpegurl", head_only)
                return
            if len(parts) == 3 and parts[0] == "hls" and parts[2].endswith(".ts"):
                session = self._session(parts[1])
                if session is None:
                    self._send(handler, 404, b"session not found", "text/plain", head_only)
                    return
                index = int(parts[2].split(".", 1)[0])
                data = session.get(index)
                session.requests += 1
                session.bytes_served += len(data)
                self._send(handler, 200, data, "video/mp2t", head_only)
                return
            self._send(handler, 404, b"not found", "text/plain", head_only)
        except IndexError:
            self._send(handler, 404, b"segment out of range", "text/plain", head_only)
        except Exception as exc:  # noqa: BLE001 - 单个分片失败不影响整体
            log.warning("分片请求失败 %s: %s", path, exc)
            self._send(handler, 502, str(exc).encode("utf-8", "ignore"), "text/plain", head_only)

    def _session(self, session_id: str) -> Optional[HlsSession]:
        with self._lock:
            return self._sessions.get(session_id)

    @staticmethod
    def _send(handler, status: int, body: bytes, content_type: str, head_only: bool) -> None:
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Connection", "close")
            handler.end_headers()
            if not head_only:
                handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        handler.close_connection = True

    # ---------- 缓存清理 ----------
    def _prune(self) -> None:
        if self.cache_root is None or not self.cache_root.exists():
            return
        entries = []
        total = 0
        for child in self.cache_root.iterdir():
            try:
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            except OSError:
                continue
            mtime = child.stat().st_mtime if child.exists() else 0.0
            entries.append((mtime, size, child))
            total += size
        entries.sort()
        while total > self.cache_limit and entries:
            _, size, child = entries.pop(0)
            shutil.rmtree(child, ignore_errors=True)
            total -= size
