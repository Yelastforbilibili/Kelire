"""HTTP 会话与站点安全校验（Cloudflare 前的 JS Cookie 校验）处理。"""

from __future__ import annotations

import re
import threading
from typing import Optional
from urllib.parse import urlsplit

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)

BROWSER_HEADERS = {
    "user-agent": DEFAULT_UA,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9",
}

MEDIA_HEADERS = {
    "user-agent": DEFAULT_UA,
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
}

_CHALLENGE_MARK = "fromCharCode(c^"
_COOKIE_STMT = re.compile(r'document\.cookie\s*=\s*"([^";]+);')
_MAP_RE = re.compile(r"\[([\d,\s]+)\]\.map\(function\(c\)\{return String\.fromCharCode\(c\^(\d+)\)")


class NetworkError(Exception):
    """网络层失败，需要切换镜像或提示用户。"""


class ChallengeError(NetworkError):
    """页面停留在安全校验且无法自动通过。"""


def solve_challenge(html: str) -> Optional[str]:
    """还原 Security Check 页面里的 document.cookie 指令，返回 "k=v"。"""
    m = _MAP_RE.search(html)
    if not m:
        return None
    try:
        codes = [int(x) for x in m.group(1).split(",") if x.strip()]
        key = int(m.group(2))
        payload = "".join(chr(code ^ key) for code in codes)
    except (ValueError, OverflowError):
        return None
    stmt = _COOKIE_STMT.search(payload)
    return stmt.group(1) if stmt else None


def looks_like_challenge(text: str) -> bool:
    head = text[:1500]
    return "Security Check" in head or _CHALLENGE_MARK in head


def _headers_for(host: str, cookies: dict[str, dict[str, str]]) -> dict[str, str]:
    headers = dict(BROWSER_HEADERS)
    jar = cookies.get(host)
    if jar:
        headers["cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
    else:
        headers["cookie"] = "showBtn=true"
    return headers


class HttpClient:
    """带安全校验自愈的 HTML 客户端。"""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._cookies: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def warm_cookie(self, host: str, pair: str) -> None:
        with self._lock:
            self._cookies.setdefault(host, {}).update(_split_pair(pair))

    def get_text(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        timeout: Optional[float] = None,
        allow_challenge_retry: bool = True,
    ) -> str:
        host = urlsplit(url).netloc
        for attempt in range(3):
            headers = _headers_for(host, self._cookies)
            if referer:
                headers["referer"] = referer
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout or self.timeout)
            except requests.RequestException as exc:
                raise NetworkError(f"请求失败：{exc}") from exc
            if resp.status_code >= 400:
                raise NetworkError(f"HTTP {resp.status_code}：{url}")
            text = resp.text
            if looks_like_challenge(text):
                pair = solve_challenge(text) if allow_challenge_retry else None
                if not pair:
                    raise ChallengeError("站点安全校验未通过")
                self.warm_cookie(host, pair)
                continue
            return text
        raise ChallengeError("站点安全校验反复出现")


def _split_pair(pair: str) -> dict[str, str]:
    if "=" not in pair:
        return {}
    key, value = pair.split("=", 1)
    key, value = key.strip(), value.strip()
    return {key: value} if key else {}


class MediaClient:
    """分片/密钥下载用，线程内复用连接。"""

    def __init__(self, session_pool: int = 64) -> None:
        self.session_pool = session_pool
        self._local = threading.local()

    def session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.trust_env = False
            sess.headers.update(MEDIA_HEADERS)
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            self._local.session = sess
        return sess

    def _get(self, url: str, timeout: float, retries: int, referer: str) -> requests.Response:
        headers = {"referer": referer} if referer else None
        last: Optional[Exception] = None
        for _ in range(retries + 1):
            try:
                resp = self.session().get(url, timeout=timeout, headers=headers)
                if resp.status_code >= 400:
                    raise NetworkError(f"HTTP {resp.status_code}")
                return resp
            except (requests.RequestException, NetworkError) as exc:
                last = exc
        raise NetworkError(f"媒体下载失败：{last}")

    def get_bytes(
        self, url: str, *, timeout: float = 30.0, retries: int = 2, referer: str = ""
    ) -> bytes:
        return self._get(url, timeout, retries, referer).content

    def get_text(
        self, url: str, *, timeout: float = 30.0, retries: int = 2, referer: str = ""
    ) -> str:
        resp = self._get(url, timeout, retries, referer)
        return resp.text
