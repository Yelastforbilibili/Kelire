"""镜像发现与测速。

行为：从地址发布页抓取同族镜像，并发探测首页响应时间，选最快可用者。
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlsplit

import requests

from .http import BROWSER_HEADERS

MIRROR_PAGE = "https://www.ttdm0.me/"
_MIRROR_RE = re.compile(r"https?://(?:www\.)?ttdm\d*\.me", re.IGNORECASE)
FALLBACK_MIRRORS = (
    "https://www.ttdm.me",
    "https://www.ttdm14.me",
    "https://www.ttdm13.me",
    "https://www.ttdm12.me",
)


@dataclass
class MirrorReport:
    base_url: Optional[str] = None
    latency_ms: Optional[float] = None
    alive: list[tuple[str, float]] = field(default_factory=list)
    checked: int = 0
    error: str = ""
    updated_at: float = 0.0

    def summary(self) -> str:
        if not self.base_url:
            return "镜像不可用"
        return "%.0f ms · %d 条线路" % (self.latency_ms or 0, len(self.alive))


def _normalize(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return "https://" + host


def discover_mirrors(timeout: float = 12.0) -> list[str]:
    """抓取发布页中的镜像域名，按出现顺序去重。"""
    try:
        resp = requests.get(MIRROR_PAGE, headers=BROWSER_HEADERS, timeout=timeout)
        text = resp.text
    except requests.RequestException:
        return []
    seen: list[str] = []
    for match in _MIRROR_RE.finditer(text):
        base = _normalize(match.group(0))
        if base not in seen:
            seen.append(base)
    return seen


def probe_mirror(base: str, timeout: float = 6.0) -> Optional[float]:
    """返回首页响应耗时（毫秒）；不可用返回 None。"""
    started = time.perf_counter()
    try:
        resp = requests.get(base + "/", headers=BROWSER_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or len(resp.text) < 4000:
        return None
    if "天天动漫" not in resp.text and "module-poster-item" not in resp.text:
        return None
    return round((time.perf_counter() - started) * 1000, 1)


def refresh_mirrors(
    preferred: Optional[str] = None,
    extra: Iterable[str] = (),
    timeout: float = 6.0,
) -> MirrorReport:
    """完整刷新流程：发现 → 测速 → 选最优。"""
    report = MirrorReport(updated_at=time.time())
    try:
        hosts = discover_mirrors()
    except Exception as exc:  # noqa: BLE001 - 网络层异常统一降级
        hosts = []
        report.error = str(exc)
    candidates: list[str] = []
    for url in [preferred or "", *[h for h in hosts], *[e for e in extra], *FALLBACK_MIRRORS]:
        if not url:
            continue
        base = _normalize(url)
        if base not in candidates:
            candidates.append(base)
    if not candidates:
        report.error = report.error or "未发现可用镜像"
        return report

    report.checked = len(candidates)
    results: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        for base, latency in zip(candidates, pool.map(lambda b: probe_mirror(b, timeout), candidates)):
            if latency is not None:
                results.append((base, latency))
    results.sort(key=lambda pair: pair[1])
    report.alive = results
    if results:
        report.base_url, report.latency_ms = results[0]
    else:
        report.error = report.error or "所有镜像均无响应"
    return report
