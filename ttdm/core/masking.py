"""调试遮蔽：开启后隐藏番剧名称与图片，只放行测速用的那部作品。

用途是排查「某个条目在界面上显示异常」时，避免整页都是实际内容，
同时保留一个能正常加载的样本做对照。
"""

from __future__ import annotations

from typing import Optional

# 允许正常显示的关键词（测速常用的那部，覆盖第二季标题）
ALLOW_KEYWORDS = ("芙莉莲",)

_HIDDEN_TEXT = "调试模式已隐藏"
_ENABLED = False
_KEEP_SEARCH = True
_ALIASES: dict[str, str] = {}


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = bool(value)


def enabled() -> bool:
    return _ENABLED


def set_keep_search(value: bool) -> None:
    """搜索结果是否豁免遮蔽：调试时靠搜索把显示异常的条目找出来。"""
    global _KEEP_SEARCH
    _KEEP_SEARCH = bool(value)


def keep_search() -> bool:
    return _KEEP_SEARCH


def allowed(*texts: Optional[str]) -> bool:
    """任意一处命中白名单关键词就视为可正常显示。"""
    for text in texts:
        if not text:
            continue
        for keyword in ALLOW_KEYWORDS:
            if keyword in text:
                return True
    return False


def alias_for(key: str) -> str:
    if key not in _ALIASES:
        _ALIASES[key] = "番剧 %02d" % (len(_ALIASES) + 1)
    return _ALIASES[key]


def name(text: Optional[str]) -> str:
    """番剧名替换为稳定别名。"""
    value = text or ""
    if not _ENABLED or allowed(value):
        return value
    return alias_for(value or "?")


def cover(url: Optional[str], name_text: Optional[str] = "") -> str:
    """图片地址清空，调用方据此跳过下载与显示。"""
    value = url or ""
    if not _ENABLED or allowed(name_text):
        return value
    return ""


def hidden(text: Optional[str], placeholder: str = _HIDDEN_TEXT) -> str:
    """介绍、演员这类描述性文本整体隐藏。"""
    value = text or ""
    if not _ENABLED or allowed(value):
        return value
    return placeholder if value.strip() else value


def path(value: Optional[str]) -> str:
    """文件路径只留文件名，避免目录里的番剧名漏出来。"""
    text = value or ""
    if not _ENABLED or not text:
        return text
    if allowed(text):
        return text
    tail = text.replace("\\", "/").rstrip("/").split("/")[-1]
    return "…/%s" % tail if tail else _HIDDEN_TEXT
