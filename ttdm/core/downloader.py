"""基于 yt-dlp 的剧集下载器：分片并发、进度回调、断点续传、可取消。

yt-dlp 负责 m3u8 解析、AES-128 分片解密与合并；这里只负责把
站点播放地址翻译成 yt-dlp 需要的输入，并把进度/结果整理成 GUI 能用的结构。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import yt_dlp
from yt_dlp import utils as ytdlp_utils

from ..config import Settings
from ..paths import safe_filename
from .http import DEFAULT_UA
from .site import AnimeDetail, Episode, PlaySource, SiteApi

log = logging.getLogger(__name__)

_TRAILING_NOISE = re.compile(r"(完结|更新至|全|\s)+$")


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


@dataclass
class DownloadProgress:
    task_id: str
    vod_id: str
    name: str
    poster: str
    sid: int
    source_name: str
    episode_label: str
    episode_index: int
    episode_total: int
    target: str = ""
    status: str = "queued"  # queued / downloading / finished / error / canceled
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    eta: int = 0
    error: str = ""

    @property
    def percent(self) -> float:
        if self.status == "finished":
            return 100.0
        if self.total > 0:
            return min(99.0, self.downloaded * 100.0 / self.total)
        return 0.0

    @property
    def active(self) -> bool:
        return self.status in ("queued", "downloading")


@dataclass
class _Job:
    progress: DownloadProgress
    referer: str
    directory: Path
    stem: str
    cancel: threading.Event
    episode: Optional[Episode] = None
    fragments: int = 0
    fragment_index: int = 0


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        return

    def info(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        return

    def error(self, msg: str) -> None:
        text = str(msg).strip()
        if text:
            self.errors.append(text)


def _ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 缺少 ffmpeg 时退回 yt-dlp 自带逻辑
        return ""


def episode_stem(index: int, label: str) -> str:
    """生成稳定、可排序的集数文件名，如 第01集 / 第10集 / 正片。"""
    text = (label or "").strip()
    text = _TRAILING_NOISE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "%02d" % index
    padded = re.sub(r"\d+", lambda m: m.group(0).zfill(2), text)
    if not re.search(r"\d", text):
        padded = "%02d %s" % (index, text)
    return safe_filename(padded)


_TEMPLATE_TOKENS = {
    "{集数}": "episode",
    "{集号}": "index",
    "{名字}": "name",
    "{总数}": "total",
    "{线路}": "source",
    "{episode}": "episode",
    "{index}": "index",
    "{name}": "name",
    "{total}": "total",
    "{source}": "source",
}


def format_stem(
    template: str,
    index: int,
    label: str,
    name: str,
    total: int,
    source: str = "",
) -> str:
    """按用户模板拼文件名，未识别的占位符原样保留便于发现写错。"""
    text = (template or "{集数}").strip() or "{集数}"
    values = {
        "episode": episode_stem(index, label),
        "index": "%02d" % index,
        "name": name or "",
        "total": str(total),
        "source": source or "",
    }
    for token, key in _TEMPLATE_TOKENS.items():
        if token in text:
            text = text.replace(token, values[key])
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = values["episode"]
    return safe_filename(text)


def _existing_output(directory: Path, stem: str) -> Optional[Path]:
    for suffix in (".mp4", ".mkv", ".ts", ".webm"):
        candidate = directory / (stem + suffix)
        if candidate.exists() and candidate.stat().st_size > 64 * 1024:
            return candidate
    return None


def _remux_to_mp4(ffmpeg: str, source: Path) -> Optional[Path]:
    if not ffmpeg or source.suffix == ".mp4":
        return None
    target = source.with_suffix(".mp4")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-c", "copy", "-bsf:a", "aac_adtstoasc", str(target)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    except (subprocess.SubprocessError, OSError):
        return None
    if target.exists() and target.stat().st_size > 0:
        try:
            source.unlink()
        except OSError:
            pass
        return target
    return None


class DownloadManager:
    """把一批剧集放进线程池并行下载，事件回调在工作线程触发。"""

    def __init__(
        self,
        api: SiteApi,
        settings_provider: Callable[[], Settings],
        on_event: Callable[[DownloadProgress], None],
    ) -> None:
        self.api = api
        self._settings = settings_provider
        self._on_event = on_event
        self._ffmpeg = _ffmpeg_path()
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._pool: Optional[ThreadPoolExecutor] = None
        self._pool_size = 0

    # ---------- 线程池 ----------
    def _executor(self, parallel: int) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None or self._pool_size != parallel:
                if self._pool is not None:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="dl")
                self._pool_size = parallel
            return self._pool

    def shutdown(self) -> None:
        with self._lock:
            pool, self._pool = self._pool, None
        for job in list(self._jobs.values()):
            job.cancel.set()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    # ---------- 入队 ----------
    def enqueue(
        self,
        detail: AnimeDetail,
        source: PlaySource,
        episodes: Iterable[Episode],
        base_dir: Path,
    ) -> list[str]:
        settings = self._settings()
        workers = max(4, settings.download_workers)
        parallel = max(1, settings.download_parallel)
        folder = Path(base_dir) / safe_filename(detail.name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("无法创建下载目录 %s: %s", folder, exc)
            return []

        episode_list = list(episodes)
        total = len(episode_list)
        pool = self._executor(parallel)
        created: list[str] = []
        for position, episode in enumerate(episode_list, start=1):
            task_id = "%s-%d-%d" % (detail.vod_id, source.sid, episode.nid)
            with self._lock:
                if task_id in self._jobs:
                    continue
            progress = DownloadProgress(
                task_id=task_id,
                vod_id=detail.vod_id,
                name=detail.name,
                poster=detail.cover,
                sid=source.sid,
                source_name=source.name,
                episode_label=episode.label,
                episode_index=position,
                episode_total=total,
            )
            stem = format_stem(
                settings.filename_template,
                position,
                episode.label,
                detail.name,
                total,
                source.name,
            )
            job = _Job(
                progress=progress,
                referer="%s/vod/%s/" % (self.api.base, detail.vod_id),
                directory=folder,
                stem=stem,
                cancel=threading.Event(),
                episode=episode,
            )
            with self._lock:
                self._jobs[task_id] = job
            created.append(task_id)
            self._emit(progress)
            pool.submit(self._run, job, episode, workers)
        return created

    # ---------- 执行 ----------
    def _emit(self, progress: DownloadProgress) -> None:
        try:
            self._on_event(progress)
        except Exception:  # noqa: BLE001 - 回调异常不能杀死下载线程
            log.debug("下载事件回调异常", exc_info=True)

    def _run(self, job: _Job, episode: Episode, workers: int) -> None:
        progress = job.progress
        existing = _existing_output(job.directory, job.stem)
        if existing is not None:
            progress.status = "finished"
            progress.target = str(existing)
            progress.total = progress.downloaded = existing.stat().st_size
            self._emit(progress)
            return
        if job.cancel.is_set():
            progress.status = "canceled"
            self._emit(progress)
            return

        progress.status = "downloading"
        self._emit(progress)
        try:
            info = self.api.play_info(progress.vod_id, progress.sid, episode.nid)
        except Exception as exc:  # noqa: BLE001 - 线路失效等
            progress.status = "error"
            progress.error = str(exc) or type(exc).__name__
            self._emit(progress)
            return

        logger = _Logger()
        params = {
            "outtmpl": {"default": str(job.directory / (job.stem + ".%(ext)s"))},
            "format": "best",
            "concurrent_fragment_downloads": workers,
            "hls_prefer_native": True,
            "retries": 5,
            "fragment_retries": 10,
            "socket_timeout": 30,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "overwrites": False,
            "continuedl": True,
            "logger": logger,
            "http_headers": {
                "User-Agent": DEFAULT_UA,
                "Referer": job.referer,
                "Origin": self.api.base,
            },
            "progress_hooks": [lambda d: self._on_ytdlp(job, d)],
        }
        if self._ffmpeg:
            params["ffmpeg_location"] = self._ffmpeg
        started = time.time()
        try:
            with yt_dlp.YoutubeDL(params) as ydl:
                ydl.extract_info(info.url, download=True)
        except ytdlp_utils.DownloadCancelled:
            progress.status = "canceled"
            self._emit(progress)
            return
        except Exception as exc:  # noqa: BLE001 - yt-dlp 的异常类型很多
            if job.cancel.is_set():
                progress.status = "canceled"
            else:
                progress.status = "error"
                detail = str(exc).strip().splitlines()
                progress.error = detail[0][:180] if detail else type(exc).__name__
                if logger.errors and not progress.error:
                    progress.error = logger.errors[0][:180]
            self._emit(progress)
            return

        finished = _existing_output(job.directory, job.stem)
        if finished is None:
            finished = self._pick_output(job)
        if finished is not None and finished.suffix != ".mp4":
            remuxed = _remux_to_mp4(self._ffmpeg, finished)
            if remuxed is not None:
                finished = remuxed
        if finished is None:
            progress.status = "error"
            progress.error = "下载完成但未找到输出文件"
            self._emit(progress)
            return
        progress.status = "finished"
        progress.target = str(finished)
        progress.total = progress.downloaded = finished.stat().st_size
        progress.speed = 0.0
        progress.eta = 0
        log.info("下载完成 %s -> %s (%.1fs)", progress.name, finished.name, time.time() - started)
        self._emit(progress)

    def _pick_output(self, job: _Job) -> Optional[Path]:
        candidates = [
            path
            for path in job.directory.glob(job.stem + ".*")
            if path.is_file() and path.suffix not in (".part", ".ytdl", ".tmp")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda p: (p.suffix != ".mp4", -p.stat().st_size))
        return candidates[0]

    def _on_ytdlp(self, job: _Job, data: dict) -> None:
        progress = job.progress
        if job.cancel.is_set():
            raise ytdlp_utils.DownloadCancelled("已取消")
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            fragments = data.get("fragment_count") or 0
            index = data.get("fragment_index") or 0
            if fragments and index:
                job.fragments, job.fragment_index = int(fragments), int(index)
            if not total and fragments:
                total = fragments
                downloaded = index
            progress.status = "downloading"
            progress.total = int(total or 0)
            progress.downloaded = int(downloaded or 0)
            progress.speed = float(data.get("speed") or 0.0)
            progress.eta = int(data.get("eta") or 0)
            filename = data.get("filename") or ""
            if filename:
                progress.target = filename
            self._emit(progress)
        elif status == "finished":
            progress.downloaded = progress.total = int(data.get("total_bytes") or progress.total)
            self._emit(progress)

    # ---------- 取消 ----------
    def cancel(self, task_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(task_id)
        if job is None:
            return False
        job.cancel.set()
        if job.progress.status == "queued":
            job.progress.status = "canceled"
            self._emit(job.progress)
        return True

    def cancel_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
            if job.progress.status == "queued":
                job.progress.status = "canceled"
                self._emit(job.progress)

    def forget_finished(self) -> None:
        with self._lock:
            done = [tid for tid, job in self._jobs.items() if not job.progress.active]
            for tid in done:
                self._jobs.pop(tid, None)

    # ---------- 状态查询 ----------
    def target_of(self, task_id: str) -> Optional[str]:
        with self._lock:
            job = self._jobs.get(task_id)
        if job is None:
            return None
        path = job.progress.target
        if path and Path(path).exists():
            return path
        existing = _existing_output(job.directory, job.stem)
        if existing is not None:
            return str(existing)
        return path or None

    def retry(self, task_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(task_id)
            if job is None or job.progress.active or job.episode is None:
                return False
            job.cancel = threading.Event()
        settings = self._settings()
        workers = max(4, settings.download_workers)
        parallel = max(1, settings.download_parallel)
        progress = job.progress
        progress.status = "queued"
        progress.error = ""
        progress.downloaded = 0
        progress.total = 0
        progress.speed = 0.0
        progress.eta = 0
        self._emit(progress)
        self._executor(parallel).submit(self._run, job, job.episode, workers)
        return True
