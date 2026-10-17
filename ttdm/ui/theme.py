"""视觉系统：颜色令牌、字体、全局 QSS 与少量绘制辅助。

风格取向：影院级深色（Modern Dark Cinema）为默认，另有一套等价的浅色。
绘制一律读本模块令牌，切换主题时 set_mode() 后统一重绘即可。
"""

from __future__ import annotations

import ctypes
import re
import sys

from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPalette, QRadialGradient
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

# ---------------------------------------------------------------- 颜色令牌

DARK = {
    "BG_DEEP": "#090C11",
    "BG": "#0B0E14",
    "BG_ALT": "#0E1218",
    "SURFACE": "#14181F",
    "SURFACE_HI": "#181D26",
    "SURFACE_HOVER": "#1D232E",
    "SURFACE_SUNK": "#0F131A",
    "SURFACE_FOCUS": "#10141B",
    "BORDER": "rgba(255,255,255,20)",
    "BORDER_SOFT": "rgba(255,255,255,12)",
    "BORDER_STRONG": "rgba(255,255,255,44)",
    "FILL": "rgba(255,255,255,8)",
    "HOVER": "rgba(255,255,255,10)",
    "HOVER_MID": "rgba(255,255,255,17)",
    "HOVER_STRONG": "rgba(255,255,255,22)",
    "TRACK": "rgba(255,255,255,28)",
    "TRACK_STRONG": "rgba(255,255,255,34)",
    "TRACK_HOVER": "rgba(255,255,255,62)",
    "TEXT": "#EDEFF3",
    "TEXT_DIM": "#98A0AE",
    "TEXT_FAINT": "#616A7A",
    "ON_ACCENT": "#FFFFFF",
    "ACCENT": "#E2404F",
    "ACCENT_HOVER": "#F05263",
    "ACCENT_DARK": "#B62B38",
    "GOLD": "#E8B15C",
    "GREEN": "#41C08A",
    "BLUE": "#5B93FF",
    "SHADOW": "#000000",
    "GLOW": (226, 64, 79, 34),
    "COVER_TOP": "#1B2230",
    "COVER_BOTTOM": "#131821",
    "BADGE_BG": "rgba(9,12,17,196)",
    "NAV_CHECKED_BG": "rgba(226,64,79,38)",
    "NAV_CHECKED_TEXT": "#EDEFF3",
    "NAV_CHECKED_BORDER": "rgba(226,64,79,96)",
}

LIGHT = {
    "BG_DEEP": "#EEF0F4",
    "BG": "#F6F7FA",
    "BG_ALT": "#FFFFFF",
    "SURFACE": "#FFFFFF",
    "SURFACE_HI": "#F1F3F7",
    "SURFACE_HOVER": "#E9ECF2",
    "SURFACE_SUNK": "#F3F4F8",
    "SURFACE_FOCUS": "#FFFFFF",
    "BORDER": "rgba(17,24,38,26)",
    "BORDER_SOFT": "rgba(17,24,38,15)",
    "BORDER_STRONG": "rgba(17,24,38,62)",
    "FILL": "rgba(17,24,38,6)",
    "HOVER": "rgba(17,24,38,9)",
    "HOVER_MID": "rgba(17,24,38,15)",
    "HOVER_STRONG": "rgba(17,24,38,21)",
    "TRACK": "rgba(17,24,38,18)",
    "TRACK_STRONG": "rgba(17,24,38,30)",
    "TRACK_HOVER": "rgba(17,24,38,56)",
    "TEXT": "#151A22",
    "TEXT_DIM": "#5B6573",
    "TEXT_FAINT": "#8B94A3",
    "ON_ACCENT": "#FFFFFF",
    # 浅色底下红色太扎眼，换成沉稳的靛蓝，白色文字压得住
    "ACCENT": "#3D5BD9",
    "ACCENT_HOVER": "#4F6CE8",
    "ACCENT_DARK": "#2C41A6",
    "GOLD": "#A9741E",
    "GREEN": "#17855A",
    "BLUE": "#2F8A7A",
    "SHADOW": "#1B2230",
    "GLOW": (61, 91, 217, 16),
    "COVER_TOP": "#DDE2EA",
    "COVER_BOTTOM": "#C9D0DA",
    "BADGE_BG": "rgba(17,24,38,186)",
    "NAV_CHECKED_BG": "rgba(61,91,217,20)",
    "NAV_CHECKED_TEXT": "#2C41A6",
    "NAV_CHECKED_BORDER": "rgba(61,91,217,115)",

}

PRESETS = {"dark": DARK, "light": LIGHT}
MODE = "dark"

BG_DEEP = DARK["BG_DEEP"]
BG = DARK["BG"]
BG_ALT = DARK["BG_ALT"]
SURFACE = DARK["SURFACE"]
SURFACE_HI = DARK["SURFACE_HI"]
SURFACE_HOVER = DARK["SURFACE_HOVER"]
SURFACE_SUNK = DARK["SURFACE_SUNK"]
SURFACE_FOCUS = DARK["SURFACE_FOCUS"]
BORDER = DARK["BORDER"]
BORDER_SOFT = DARK["BORDER_SOFT"]
BORDER_STRONG = DARK["BORDER_STRONG"]
FILL = DARK["FILL"]
HOVER = DARK["HOVER"]
HOVER_MID = DARK["HOVER_MID"]
HOVER_STRONG = DARK["HOVER_STRONG"]
TRACK = DARK["TRACK"]
TRACK_STRONG = DARK["TRACK_STRONG"]
TRACK_HOVER = DARK["TRACK_HOVER"]
TEXT = DARK["TEXT"]
TEXT_DIM = DARK["TEXT_DIM"]
TEXT_FAINT = DARK["TEXT_FAINT"]
ON_ACCENT = DARK["ON_ACCENT"]
ACCENT = DARK["ACCENT"]
ACCENT_HOVER = DARK["ACCENT_HOVER"]
ACCENT_DARK = DARK["ACCENT_DARK"]
GOLD = DARK["GOLD"]
GREEN = DARK["GREEN"]
BLUE = DARK["BLUE"]
SHADOW = DARK["SHADOW"]
GLOW = DARK["GLOW"]
COVER_TOP = DARK["COVER_TOP"]
COVER_BOTTOM = DARK["COVER_BOTTOM"]
BADGE_BG = DARK["BADGE_BG"]
NAV_CHECKED_BG = DARK["NAV_CHECKED_BG"]
NAV_CHECKED_TEXT = DARK["NAV_CHECKED_TEXT"]
NAV_CHECKED_BORDER = DARK["NAV_CHECKED_BORDER"]

FONT_STACK = "Microsoft YaHei UI"

# 强调色：默认值 + 设置里可选的预设（也可以自定义任意颜色）
DEFAULT_ACCENTS = {"dark": "#E2404F", "light": "#3D5BD9"}
PRESET_ACCENTS = ("#E2404F", "#3D5BD9", "#0E9AA7", "#7C5CFF", "#D98E04", "#2FA36B")


def set_mode(mode: str) -> bool:
    """切换令牌。返回是否真的变化（调用方据此决定要不要重绘）。"""
    global MODE
    mode = mode if mode in PRESETS else "dark"
    if mode == MODE:
        return False
    MODE = mode
    module = sys.modules[__name__]
    for key, value in PRESETS[mode].items():
        setattr(module, key, value)
    return True


def _mix(color: QColor, target: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(round(color.red() * (1 - ratio) + target.red() * ratio)),
        int(round(color.green() * (1 - ratio) + target.green() * ratio)),
        int(round(color.blue() * (1 - ratio) + target.blue() * ratio)),
    )


def accent_default(mode: str = "") -> str:
    return DEFAULT_ACCENTS.get(mode or MODE, DEFAULT_ACCENTS["dark"])


def accent(mode: str = "") -> str:
    return PRESETS.get(mode or MODE, PRESETS["dark"])["ACCENT"]


def set_accent(mode: str, color: str) -> bool:
    """把某个主题的强调色换成自定义色（空字符串＝恢复默认），派生出 hover/dark/辉光/导航色。"""
    mode = mode if mode in PRESETS else "dark"
    palette = PRESETS[mode]
    base = QColor((color or "").strip() or accent_default(mode))
    if not base.isValid():
        return False
    hover = _mix(base, QColor("#FFFFFF"), 0.14 if mode == "dark" else 0.10)
    dark = _mix(base, QColor("#000000"), 0.16 if mode == "dark" else 0.22)
    on_accent = "#FFFFFF" if base.lightnessF() < 0.76 else "#11141A"
    red, green, blue = base.red(), base.green(), base.blue()
    palette["ACCENT"] = base.name()
    palette["ACCENT_HOVER"] = hover.name()
    palette["ACCENT_DARK"] = dark.name()
    palette["ON_ACCENT"] = on_accent
    palette["GLOW"] = (red, green, blue, 34 if mode == "dark" else 16)
    palette["NAV_CHECKED_BG"] = "rgba(%d,%d,%d,%d)" % (red, green, blue, 38 if mode == "dark" else 20)
    palette["NAV_CHECKED_TEXT"] = base.name() if mode == "dark" else dark.name()
    palette["NAV_CHECKED_BORDER"] = "rgba(%d,%d,%d,%d)" % (
        red,
        green,
        blue,
        96 if mode == "dark" else 120,
    )
    # 自定义色也要能被「图标颜色反查」认出来，切主题时才会跟着重刷
    _TOKEN_BY_COLOR[base.name().upper()] = "ACCENT"
    _TOKEN_BY_COLOR[hover.name().upper()] = "ACCENT_HOVER"
    _TOKEN_BY_COLOR[dark.name().upper()] = "ACCENT_DARK"
    if mode == MODE:
        module = sys.modules[__name__]
        for key in (
            "ACCENT",
            "ACCENT_HOVER",
            "ACCENT_DARK",
            "ON_ACCENT",
            "GLOW",
            "NAV_CHECKED_BG",
            "NAV_CHECKED_TEXT",
            "NAV_CHECKED_BORDER",
        ):
            setattr(module, key, palette[key])
    return True


def is_dark() -> bool:
    return MODE == "dark"


REDUCE_MOTION = False


def motion(duration: int) -> int:
    """按「减少动效」设置返回实际时长；0 表示直接跳到终态。"""
    return 0 if REDUCE_MOTION else duration


# 具体色值反查令牌名：图标这类「颜色在构造时烘焙」的地方，切主题时要重刷。
_TOKEN_BY_COLOR: dict[str, str] = {}
for _preset in (LIGHT, DARK):
    for _key, _value in _preset.items():
        if isinstance(_value, str) and _key not in ("ON_ACCENT", "SHADOW"):
            _TOKEN_BY_COLOR.setdefault(_value.upper(), _key)


def relabel(color: str) -> str:
    """把一个历史色值换成当前模式下的对应令牌值。"""
    name = _TOKEN_BY_COLOR.get(color.upper())
    if name is None:
        return color
    return globals().get(name, color)


def alpha(color: str, value: int) -> str:
    """把 #RRGGBB 转成 Qt 可用的 rgba()，value 为 0-255。"""
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return "rgba(%d,%d,%d,%d)" % (r, g, b, value)


def qcolor(color: str) -> QColor:
    return QColor(relabel(color))


_INK_MAP = {8: "FILL", 10: "HOVER", 16: "HOVER_MID", 17: "HOVER_MID", 18: "HOVER_MID", 20: "BORDER", 22: "HOVER_STRONG", 26: "BORDER", 28: "TRACK", 34: "TRACK_STRONG", 44: "BORDER_STRONG", 62: "TRACK_HOVER"}


def _ink(alpha_value: int) -> str:
    key = _INK_MAP.get(alpha_value)
    if key is None:
        return globals().get("HOVER_MID", "rgba(255,255,255,17)")
    return globals()[key]


_INK_RE = re.compile(r"rgba\(255,\s*255,\s*255,\s*(\d+)\)")


def _pick_font() -> str:
    families = set(QFontDatabase().families())
    for name in (FONT_STACK, "Microsoft YaHei", "Segoe UI", "Noto Sans SC", "Source Han Sans SC"):
        if name in families:
            return name
    return "Segoe UI"


def apply_theme(app: QApplication) -> None:
    family = _pick_font()
    base = QFont(family, 10)
    base.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(base)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor(SURFACE_HI))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(ON_ACCENT))
    palette.setColor(QPalette.ToolTipBase, QColor(SURFACE_HI))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_FAINT))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_FAINT))
    app.setPalette(palette)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(family))
    _install_message_filter()


def apply_mode(app: QApplication, mode: str) -> bool:
    """切换配色并整体重刷样式，返回是否发生了变化。"""
    if not set_mode(mode):
        return False
    apply_theme(app)
    return True


_NOISE = "unique connections require a pointer to member function"


def _install_message_filter() -> None:
    """屏蔽 PySide 对 Python 自定义信号连接的无害提示，其余 Qt 警告照常输出。"""
    if getattr(_install_message_filter, "_done", False):
        return
    _install_message_filter._done = True  # type: ignore[attr-defined]

    def handler(mode, context, message: str) -> None:
        if _NOISE in message:
            return
        try:
            sys.stderr.write(message + "\n")
        except Exception:  # noqa: BLE001
            pass

    qInstallMessageHandler(handler)


def stylesheet(family: str) -> str:
    qss = f"""
* {{
    outline: 0;
}}
QWidget {{
    color: {TEXT};
    font-family: "{family}";
}}
QToolTip {{
    background-color: {SURFACE_HI};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- 结构性容器 ---------- */
QFrame#Sidebar {{
    background-color: {BG_ALT};
    border: none;
    border-right: 1px solid {BORDER_SOFT};
}}
QFrame#TopBar {{
    background-color: transparent;
    border: none;
}}
QFrame#Card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-radius: 16px;
}}
QFrame#Panel {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-radius: 14px;
}}
QFrame#Row {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-radius: 13px;
}}
QFrame#RowMuted {{
    background-color: {SURFACE_SUNK};
    border: 1px solid {BORDER_SOFT};
    border-radius: 13px;
}}
QFrame#Divider {{
    background-color: {BORDER_SOFT};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- 文字 ---------- */
QLabel {{
    background: transparent;
}}
QLabel#H1 {{ font-size: 27px; font-weight: 700; color: {TEXT}; }}
QLabel#H2 {{ font-size: 17px; font-weight: 700; color: {TEXT}; }}
QLabel#H3 {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
QLabel#Muted {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Faint {{ color: {TEXT_FAINT}; font-size: 11px; }}
QLabel#Eyebrow {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#Brand {{
    font-size: 18px;
    font-weight: 800;
    color: {TEXT};
}}

QLabel#Display {{
    font-size: 26px;
    font-weight: 800;
    color: {TEXT};
}}
QLabel#FieldTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#RowTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#RowMeta {{
    font-size: 13px;
    color: {TEXT_DIM};
}}
QLabel#CardTitle {{
    font-size: 12.5px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#CardMeta {{
    font-size: 11px;
    color: {TEXT_FAINT};
}}
QLabel#Micro {{
    font-size: 10px;
    color: {TEXT_FAINT};
}}
QLabel#ToastText {{
    font-size: 12.5px;
    color: {TEXT};
}}
QDialog#Sheet {{
    background: {SURFACE};
}}

/* ---------- 侧边导航 ---------- */
QPushButton#NavButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 9px 12px;
    text-align: left;
    color: {TEXT_DIM};
    font-size: 13px;
}}
QPushButton#NavButton:hover {{
    background-color: rgba(255,255,255,10);
    color: {TEXT};
}}
QPushButton#NavButton:checked {{
    background-color: {NAV_CHECKED_BG};
    border: 1px solid {NAV_CHECKED_BORDER};
    color: {NAV_CHECKED_TEXT};
    font-weight: 600;
}}
QPushButton#NavButton:checked:hover {{
    background-color: {alpha(ACCENT, 46)};
}}

/* ---------- 按钮 ---------- */
QPushButton#Primary {{
    background-color: {ACCENT};
    color: {ON_ACCENT};
    border: none;
    border-radius: 10px;
    padding: 9px 20px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background-color: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background-color: {alpha(ACCENT, 60)}; color: rgba(255,255,255,120); }}

QPushButton#Ghost {{
    background-color: rgba(255,255,255,10);
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 9px 18px;
    font-size: 13px;
}}
QPushButton#Ghost:hover {{ background-color: rgba(255,255,255,22); border-color: {BORDER_STRONG}; }}
QPushButton#Ghost:pressed {{ background-color: rgba(255,255,255,8); }}
QPushButton#Ghost:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER_SOFT}; }}

QPushButton#Subtle {{
    background: transparent;
    color: {TEXT_DIM};
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}
QPushButton#Subtle:hover {{ color: {TEXT}; background-color: rgba(255,255,255,10); }}
QPushButton#Subtle:checked {{ color: {TEXT}; background-color: rgba(255,255,255,16); }}

QPushButton#IconButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 0px;
}}
QPushButton#IconButton:hover {{ background-color: rgba(255,255,255,16); border-color: {BORDER_SOFT}; }}
QPushButton#IconButton:pressed {{ background-color: rgba(255,255,255,8); }}

QPushButton#Chip {{
    background-color: rgba(255,255,255,10);
    border: 1px solid {BORDER_SOFT};
    border-radius: 13px;
    padding: 5px 13px;
    color: {TEXT_DIM};
    font-size: 12px;
}}
QPushButton#Chip:hover {{ color: {TEXT}; background-color: rgba(255,255,255,18); }}
QPushButton#Chip:checked {{ color: {ON_ACCENT}; background-color: {alpha(ACCENT, 46)}; border-color: {alpha(ACCENT, 110)}; }}

QPushButton#TabButton {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 2px;
    color: {TEXT_FAINT};
    font-size: 12px;
}}
QPushButton#TabButton:hover {{ color: {TEXT_DIM}; }}
QPushButton#TabButton:checked {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QPushButton#Episode {{
    background-color: rgba(255,255,255,8);
    border: 1px solid {BORDER_SOFT};
    border-radius: 9px;
    padding: 8px 10px;
    color: {TEXT_DIM};
    font-size: 12px;
}}
QPushButton#Episode:hover {{ background-color: {alpha(ACCENT, 34)}; border-color: {alpha(ACCENT, 90)}; color: {ON_ACCENT}; }}
QPushButton#Episode:checked {{ background-color: {alpha(ACCENT, 60)}; border-color: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}

/* ---------- 输入 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE_SUNK};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 12px;
    color: {TEXT};
    selection-background-color: {alpha(ACCENT, 120)};
    selection-color: {ON_ACCENT};
    font-size: 13px;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; background-color: {SURFACE_FOCUS}; }}
QLineEdit#SearchField {{
    background-color: {SURFACE_SUNK};
    border: 1px solid {BORDER};
    border-radius: 11px;
    padding: 9px 14px 9px 40px;
    font-size: 13px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    background: transparent;
    border: none;
}}

/* ---------- 下拉 ---------- */
QComboBox {{
    background-color: {SURFACE_SUNK};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 12px;
    color: {TEXT};
    font-size: 13px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_HI};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 4px;
    color: {TEXT};
    selection-background-color: {alpha(ACCENT, 60)};
    selection-color: {ON_ACCENT};
    outline: 0;
}}

/* ---------- 复选 / 滑块 ---------- */
QCheckBox {{ color: {TEXT_DIM}; font-size: 13px; spacing: 9px; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 5px;
    background-color: {SURFACE_SUNK};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox:hover::indicator {{ border-color: {alpha(ACCENT, 160)}; }}

QSlider::groove:horizontal {{
    height: 4px;
    background-color: rgba(255,255,255,28);
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background-color: {ACCENT};
    border-radius: 2px;
}}
QSlider::add-page:horizontal {{
    background-color: rgba(255,255,255,28);
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background-color: {TEXT};
    width: 12px;
    height: 12px;
    margin: -5px 0px;
    border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{ background-color: {TEXT}; }}
QSlider#VolumeSlider::sub-page:horizontal {{ background-color: {TEXT_DIM}; }}
QSlider#SeekSlider::handle:horizontal {{ width: 13px; height: 13px; margin: -5px 0px; border-radius: 7px; }}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background-color: rgba(255,255,255,20);
    border: none;
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background-color: {ACCENT};
}}
QProgressBar#Slim {{ max-height: 4px; min-height: 4px; }}

/* ---------- 滚动 ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background-color: rgba(255,255,255,34);
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background-color: rgba(255,255,255,62); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: rgba(255,255,255,34);
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: rgba(255,255,255,62); }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ---------- 列表 ---------- */
QListWidget, QListView, QTreeWidget {{
    background: transparent;
    border: none;
    outline: 0;
}}
QListWidget::item {{ border: none; }}

/* ---------- 菜单 ---------- */
QMenu {{
    background-color: {SURFACE_HI};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
    color: {TEXT};
}}
QMenu::item {{
    padding: 7px 18px 7px 12px;
    border-radius: 7px;
    font-size: 12px;
}}
QMenu::item:selected {{ background-color: {alpha(ACCENT, 56)}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 5px 8px; }}

/* ---------- 消息框 ---------- */
QMessageBox {{ background-color: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
"""
    return _INK_RE.sub(lambda match: _ink(int(match.group(1))), qss)


# ---------------------------------------------------------------- 绘制辅助


class GradientBackground(QWidget):
    """渐变底 + 左上角弱光晕，作为主窗口根容器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        gradient = QLinearGradient(0, 0, rect.width() * 0.35, rect.height())
        gradient.setColorAt(0.0, QColor("#131926" if is_dark() else "#FFFFFF"))
        gradient.setColorAt(0.45, QColor(BG))
        gradient.setColorAt(1.0, QColor(BG_DEEP))
        painter.fillRect(rect, gradient)

        r, g, b, a = GLOW
        glow = QRadialGradient(rect.width() * 0.18, -rect.height() * 0.1, rect.width() * 0.6)
        glow.setColorAt(0.0, QColor(r, g, b, a))
        glow.setColorAt(0.55, QColor(r, g, b, max(4, a // 3)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, glow)
        painter.end()


def drop_shadow(widget: QWidget, blur: int = 28, offset: int = 8, color: str = "", opacity: int = 150) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset)
    shadow_color = QColor(color or SHADOW)
    shadow_color.setAlpha(opacity if is_dark() else min(70, opacity))
    effect.setColor(shadow_color)
    widget.setGraphicsEffect(effect)


def _bgr(hex_color: str) -> int:
    """#RRGGBB 转 DWM 需要的 0x00BBGGRR。"""
    color = hex_color.lstrip("#")
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return (b << 16) | (g << 8) | r


def enable_dark_title_bar(window: QWidget) -> None:
    """让 Windows 自带标题栏跟随当前主题（Win10 1809+ / Win11）。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if is_dark() else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        corner = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        border = ctypes.c_int(_bgr("#FFFFFF" if is_dark() else "#D8DCE4"))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        caption = ctypes.c_int(_bgr(BG_DEEP))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    except Exception:  # noqa: BLE001 - 老系统忽略即可
        pass
