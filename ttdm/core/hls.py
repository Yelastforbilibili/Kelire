"""HLS 清单解析、AES-128 解密与分片抓取。"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from Crypto.Cipher import AES

from .http import MediaClient


@dataclass
class Segment:
    index: int
    url: str
    duration: float = 0.0
    sequence: int = 0
    key_url: str = ""
    iv: bytes = b""

    @property
    def offset(self) -> float:
        return self.start

    start: float = field(default=0.0)


@dataclass
class Variant:
    url: str
    bandwidth: int = 0
    resolution: str = ""


@dataclass
class Playlist:
    is_master: bool = False
    segments: list[Segment] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    target_duration: float = 0.0

    @property
    def duration(self) -> float:
        return sum(seg.duration for seg in self.segments)


_ATTR_PATTERN = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_m3u8(text: str, base_url: str) -> Playlist:
    playlist = Playlist()
    if not text or "#EXTM3U" not in text:
        raise ValueError("不是有效的 m3u8 清单")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    media_sequence = 0
    current_duration = 0.0
    current_key_url = ""
    current_iv = b""
    index = 0
    start = 0.0
    pending_variant: Optional[float] = None
    pending_resolution = ""
    pending_bandwidth = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_sequence = int(line.split(":", 1)[1].strip())
            except ValueError:
                media_sequence = 0
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                playlist.target_duration = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("#EXT-X-KEY:"):
            attrs = {k: v.strip('"') for k, v in _ATTR_PATTERN.findall(line[len("#EXT-X-KEY:"):])}
            method = attrs.get("METHOD", "NONE").upper()
            if method == "AES-128" and attrs.get("URI"):
                current_key_url = urljoin(base_url, attrs["URI"])
                iv_text = attrs.get("IV", "")
                current_iv = _parse_iv(iv_text) if iv_text else b""
            else:
                current_key_url = ""
                current_iv = b""
        elif line.startswith("#EXTINF:"):
            raw = line.split(":", 1)[1].split(",", 1)[0].strip()
            try:
                current_duration = float(raw)
            except ValueError:
                current_duration = 0.0
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = {k: v.strip('"') for k, v in _ATTR_PATTERN.findall(line[len("#EXT-X-STREAM-INF:"):])}
            try:
                pending_bandwidth = int(attrs.get("BANDWIDTH", "0"))
            except ValueError:
                pending_bandwidth = 0
            pending_resolution = attrs.get("RESOLUTION", "")
            pending_variant = 0.0
        elif not line.startswith("#"):
            url = urljoin(base_url, line)
            if pending_variant is not None:
                playlist.variants.append(
                    Variant(url=url, bandwidth=pending_bandwidth, resolution=pending_resolution)
                )
                pending_variant = None
                pending_resolution = ""
                pending_bandwidth = 0
            else:
                sequence = media_sequence + index
                iv = current_iv or sequence.to_bytes(16, "big")
                playlist.segments.append(
                    Segment(
                        index=index,
                        url=url,
                        duration=current_duration,
                        sequence=sequence,
                        key_url=current_key_url,
                        iv=iv,
                        start=start,
                    )
                )
                index += 1
                start += current_duration
                current_duration = 0.0
        i += 1

    playlist.is_master = bool(playlist.variants) and not playlist.segments
    return playlist


def _parse_iv(text: str) -> bytes:
    text = text.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return b""
    return raw.rjust(16, b"\x00")


def decrypt_aes128(data: bytes, key: bytes, iv: bytes) -> bytes:
    if not key or len(key) != 16:
        return data
    if len(iv) != 16:
        iv = iv.rjust(16, b"\x00")
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(data)
    except ValueError:
        return data
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain


class KeyCache:
    """线程安全的 AES key 缓存。"""

    def __init__(self, client: MediaClient, limit: int = 64) -> None:
        self._client = client
        self._limit = limit
        self._lock = threading.Lock()
        self._keys: OrderedDict[str, bytes] = OrderedDict()

    def key(self, url: str) -> bytes:
        with self._lock:
            cached = self._keys.get(url)
            if cached is not None:
                self._keys.move_to_end(url)
                return cached
        data = self._client.get_bytes(url, timeout=20, retries=2)
        if not data:
            raise ValueError("密钥下载失败")
        with self._lock:
            self._keys[url] = data
            while len(self._keys) > self._limit:
                self._keys.popitem(last=False)
        return data


def playlist_signature(segments: list[Segment]) -> str:
    digest = hashlib.sha1()
    for seg in segments:
        digest.update(seg.url.encode("utf-8", "ignore"))
        digest.update(seg.key_url.encode("utf-8", "ignore"))
        digest.update(seg.iv)
    return digest.hexdigest()[:20]


def resolve_playlist(client: MediaClient, url: str, referer: str = "") -> Playlist:
    """取 m3u8；若是多码率清单则选最高码率，并返回媒体清单。"""
    text = client.get_text(url, referer=referer, timeout=25, retries=2)
    playlist = parse_m3u8(text, url)
    if playlist.is_master and playlist.variants:
        best = max(playlist.variants, key=lambda v: (v.bandwidth, v.resolution))
        text = client.get_text(best.url, referer=referer, timeout=25, retries=2)
        playlist = parse_m3u8(text, best.url)
    if not playlist.segments:
        raise ValueError("清单中没有分片")
    return playlist
