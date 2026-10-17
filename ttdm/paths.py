"""路径与常量约定。

所有可写数据放在 %LOCALAPPDATA%\\TianTianAnime 下，下载默认落在程序根目录的 downloads。
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

APP_NAME = "TianTianAnime"  # 数据目录名保持不变，避免升级后丢配置
APP_TITLE = "Kelire"
APP_VERSION = "1.0.0"

_DATA_DIR: "Path | None" = None
_DATA_FALLBACK = False

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def app_root() -> Path:
    """程序根目录：打包后为 exe 所在目录，开发时为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _candidate_data_dirs():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    yield Path(base) / APP_NAME
    yield Path(tempfile.gettempdir()) / APP_NAME


def data_dir() -> Path:
    """数据目录：优先 LOCALAPPDATA，写不进去（权限/沙箱）时退到临时目录。"""
    global _DATA_DIR, _DATA_FALLBACK
    if _DATA_DIR is not None:
        return _DATA_DIR
    for index, candidate in enumerate(_candidate_data_dirs()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        _DATA_DIR = candidate
        _DATA_FALLBACK = index > 0
        return _DATA_DIR
    _DATA_DIR = Path(tempfile.gettempdir()) / APP_NAME
    _DATA_FALLBACK = True
    return _DATA_DIR


def data_dir_is_fallback() -> bool:
    return _DATA_FALLBACK


def _ensure(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def bin_dir() -> Path:
    return _ensure(data_dir() / "bin")


def image_cache_dir() -> Path:
    return _ensure(data_dir() / "image_cache")


def hls_cache_dir() -> Path:
    return _ensure(data_dir() / "hls_cache")


def settings_file() -> Path:
    return data_dir() / "settings.json"


def default_download_dir() -> Path:
    return app_root() / "downloads"


def safe_filename(name: str, fallback: str = "未命名") -> str:
    """清洗为 Windows 合法文件名。"""
    text = (name or "").strip()
    text = _INVALID_CHARS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    if text.upper() in _RESERVED:
        text = "_" + text
    return text[:110]
