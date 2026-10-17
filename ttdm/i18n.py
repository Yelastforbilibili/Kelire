"""界面多语言：一个语言一个 JSON，放在 ttdm/locales/ 下。

维护方式参考 Minecraft 的语言文件，只是**键直接用中文原文**，译文放在值里：

    {
      "@name": "English",
      "首页": "Home",
      "设置": "Settings"
    }

这样代码里不需要发明一堆 key，语言文件本身就能对照阅读；缺条目时回退到中文原文，
漏翻不会崩。新增语言只要往 ttdm/locales/ 里丢一个 <代码>.json，设置页的下拉框会自动出现。

两类文案的处理方式：

1. 静态文案（界面骨架、按钮、提示语）：控件第一次显示时由事件过滤器自动查表替换，
   代码里保持中文即可；
2. 运行时拼接的文案（页数、集数、通知等）：调用 tr("…{n}…", n=…) 显式翻译。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLabel,
    QLineEdit,
    QWidget,
)

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "zh_CN"
DEFAULT_NAME = "简体中文"

_state: dict[str, str] = {"code": DEFAULT_LANGUAGE}
_forward: dict[str, str] = {}          # 中文原文 -> 当前语言
_back: dict[str, str] = {}             # 当前语言 -> 中文原文（仅中文界面下用于切回来）
_listeners: list[Callable[[], None]] = []
_filter: Optional["_ShowTranslator"] = None
_busy = False


# ---------------------------------------------------------------- 词表
def locales_dir() -> Path:
    return Path(__file__).with_name("locales")


def _read_catalog(code: str) -> dict[str, str]:
    if code == DEFAULT_LANGUAGE:
        return {}
    path = locales_dir() / ("%s.json" % code)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        log.debug("没有语言文件 %s", path)
        return {}
    except ValueError as exc:
        log.warning("语言文件 %s 解析失败: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("语言文件 %s 应该是对象", path)
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def available() -> list[tuple[str, str]]:
    """[(语言代码, 自称)]，中文永远在第一个。"""
    items: list[tuple[str, str]] = [(DEFAULT_LANGUAGE, DEFAULT_NAME)]
    for path in sorted(locales_dir().glob("*.json")):
        code = path.stem
        if code == DEFAULT_LANGUAGE:
            continue
        name = _read_catalog(code).get("@name") or code
        items.append((code, name))
    return items


def language() -> str:
    return _state["code"]


def set_language(code: str, notify: bool = True) -> bool:
    """切换语言；返回是否真的变了。"""
    known = {item[0] for item in available()}
    code = code if code in known else DEFAULT_LANGUAGE
    changed = code != _state["code"]
    _state["code"] = code
    _forward.clear()
    _back.clear()
    if code != DEFAULT_LANGUAGE:
        _forward.update(_read_catalog(code))
    else:
        # 回到中文时用其它语言的译文反查，能把已经翻译过的控件换回中文
        for other, _name in available():
            if other == DEFAULT_LANGUAGE:
                continue
            for source, target in _read_catalog(other).items():
                if source.startswith("@") or not target.strip():
                    continue
                _back.setdefault(target, source)
    if notify and changed:
        notify_changed()
    return changed


def tr(text: str, **fmt) -> str:
    """把中文原文换成当前语言；带占位符时用 {name} 形式。"""
    value = _forward.get(text, text)
    if fmt:
        try:
            return value.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return value
    return value


def _lookup(text: str) -> str:
    if not text:
        return text
    if text in _forward:
        return _forward[text]
    if text in _back:
        return _back[text]
    return text


# ---------------------------------------------------------------- 控件翻译
def translate_widget(widget: QWidget) -> bool:
    """翻译单个控件的文案；返回是否有改动。"""
    changed = False
    # 输入框只翻占位符，绝不翻用户输入的内容
    if isinstance(widget, QLineEdit):
        placeholder = widget.placeholderText()
        target = _lookup(placeholder)
        if target != placeholder:
            widget.setPlaceholderText(target)
            changed = True
    else:
        text_method = getattr(widget, "text", None)
        set_method = getattr(widget, "setText", None)
        if callable(text_method) and callable(set_method) and _is_text_widget(widget):
            try:
                current = text_method()
            except Exception:  # noqa: BLE001 - 少数控件 text() 需要参数
                current = None
            if isinstance(current, str) and current:
                target = _lookup(current)
                if target != current:
                    set_method(target)
                    changed = True
    if isinstance(widget, QComboBox):
        for index in range(widget.count()):
            current = widget.itemText(index)
            target = _lookup(current)
            if target != current:
                widget.setItemText(index, target)
                changed = True
    tooltip = widget.toolTip()
    if tooltip:
        target = _lookup(tooltip)
        if target != tooltip:
            widget.setToolTip(target)
            changed = True
    return changed


def _is_text_widget(widget: QWidget) -> bool:
    return isinstance(widget, (QLabel, QAbstractButton)) or (
        not isinstance(widget, QLineEdit) and hasattr(widget, "_text")
    )


def translate_tree(root: QWidget) -> int:
    """翻译整棵控件树（切换语言时用）。"""
    if root is None:
        return 0
    count = 0
    for widget in _walk(root):
        try:
            if translate_widget(widget):
                count += 1
        except RuntimeError:
            continue
    return count


def _walk(root: QWidget):
    yield root
    for child in root.findChildren(QWidget):
        yield child


class _ShowTranslator(QObject):
    """控件显示时自动套用当前语言，省掉在几百处写 tr() 的维护成本。"""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        global _busy
        if _busy or event.type() != QEvent.Show:
            return False
        if not isinstance(obj, QWidget):
            return False
        _busy = True
        try:
            translate_widget(obj)
        except RuntimeError:
            pass
        finally:
            _busy = False
        return False


def install(app) -> None:
    global _filter
    if _filter is None:
        _filter = _ShowTranslator(app)
        app.installEventFilter(_filter)


# ---------------------------------------------------------------- 变更通知
def on_change(callback: Callable[[], None]) -> None:
    _listeners.append(callback)


def notify_changed() -> None:
    for callback in list(_listeners):
        try:
            callback()
        except Exception:  # noqa: BLE001 - 单个回调失败不影响整体
            log.warning("语言切换回调失败", exc_info=True)


def retranslate(root: Optional[QWidget] = None) -> int:
    """重新套用当前语言到整棵界面。"""
    from PySide6.QtWidgets import QApplication

    roots = [root] if root is not None else list(QApplication.allWidgets())
    count = 0
    global _busy
    _busy = True
    try:
        for widget in roots:
            try:
                count += translate_tree(widget)
            except RuntimeError:
                continue
    finally:
        _busy = False
    return count
