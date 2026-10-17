"""设置页：下载、播放、线路镜像、缓存与界面偏好。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import i18n
from ...core.site import CATEGORIES
from ...i18n import tr
from ...paths import APP_TITLE, APP_VERSION, default_download_dir
from .. import theme
from ..components import CheckToggle, CollapsiblePanel, Divider, fmt_size, make_button
from ..dialogs import ConfirmDialog


def _panel(title: str, subtitle: str = "") -> tuple[CollapsiblePanel, QVBoxLayout]:
    """每个大项一个可折叠面板：点标题行展开/收起，动画在 CollapsiblePanel 里。"""
    panel = CollapsiblePanel(title, subtitle)
    return panel, panel.body_layout


def _field(title: str, hint: str, control: QWidget, stacked: bool = False) -> QWidget:
    holder = QWidget()
    if stacked:
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        label = QLabel(title)
        label.setObjectName("FieldTitle")
        layout.addWidget(label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Muted")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        layout.addWidget(control)
        return holder
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)
    text_block = QVBoxLayout()
    text_block.setSpacing(3)
    label = QLabel(title)
    label.setObjectName("FieldTitle")
    text_block.addWidget(label)
    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("Muted")
        hint_label.setWordWrap(True)
        text_block.addWidget(hint_label)
    layout.addLayout(text_block, 1)
    layout.addWidget(control, 0, Qt.AlignVCenter)
    return holder


class SettingsPage(QWidget):
    def __init__(self, ctx, shell) -> None:
        super().__init__(shell)
        self.ctx = ctx
        self.shell = shell
        self._loading = True
        self._panels: list[tuple[CollapsiblePanel, list[tuple[QWidget, str]]]] = []
        self._expanded_state: dict[CollapsiblePanel, bool] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 22, 26, 0)
        outer.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(16)
        header = QVBoxLayout()
        header.setSpacing(3)
        self.title_label = QLabel("设置")
        self.title_label.setObjectName("H1")
        header.addWidget(self.title_label)
        self.subtitle_label = QLabel("改动即时生效并写入本机配置，不需要重启程序")
        self.subtitle_label.setObjectName("Muted")
        header.addWidget(self.subtitle_label)
        header_row.addLayout(header, 1)

        # 设置项搜索：每个面板的每一行都会登记关键词，输入即过滤
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("SearchField")
        self.search_edit.setPlaceholderText("搜索设置项，例如 缓存、线路、模板")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(300)
        self.search_edit.setMinimumHeight(34)
        self.search_edit.textChanged.connect(self._filter_settings)
        header_row.addWidget(self.search_edit, 0, Qt.AlignVCenter)
        outer.addLayout(header_row)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        content = QWidget()
        self.scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 8, 26)
        body.setSpacing(14)

        panels = [
            self._build_appearance_panel(),
            self._build_download_panel(),
            self._build_categories_panel(),
            self._build_playback_panel(),
            self._build_mirror_panel(),
            self._build_storage_panel(),
            self._build_reset_panel(),
            self._build_about_panel(),
        ]
        for panel in panels:
            body.addWidget(panel)
        self._panels = [(panel, self._collect_rows(panel)) for panel in panels]
        for panel, _rows in self._panels:
            if isinstance(panel, CollapsiblePanel):
                self._expanded_state[panel] = panel.is_expanded()
                panel.toggled.connect(lambda expanded, p=panel: self._on_panel_toggled(p, expanded))

        self.no_match = QLabel("没有找到匹配的设置项，换个关键词试试")
        self.no_match.setObjectName("Muted")
        self.no_match.hide()
        body.addWidget(self.no_match)
        body.addStretch(1)

        self._loading = False

    # ---------------------------------------------------------------- 设置项搜索
    def _on_panel_toggled(self, panel: "CollapsiblePanel", expanded: bool) -> None:
        self._expanded_state[panel] = expanded

    def _collect_rows(self, panel: QWidget) -> list[tuple[QWidget, str]]:
        """把面板正文里直接排布的行登记下来，连同可搜索的文本。"""
        rows: list[tuple[QWidget, str]] = []
        layout = getattr(panel, "body_layout", None) or panel.layout()
        if layout is None:
            return rows
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None or widget is panel:
                continue
            rows.append((widget, self._row_keywords(widget)))
        return rows

    @staticmethod
    def _row_keywords(widget: QWidget) -> str:
        parts: list[str] = []

        def add(value) -> None:
            if not value:
                return
            text = str(value).strip()
            if text:
                parts.append(text.casefold())

        for child in [widget, *widget.findChildren(QWidget)]:
            getter = getattr(child, "text", None)
            if callable(getter):
                try:
                    add(getter())
                except (TypeError, RuntimeError):
                    pass
            if isinstance(child, QLineEdit):
                add(child.placeholderText())
            if isinstance(child, QComboBox):
                for row in range(child.count()):
                    add(child.itemText(row))
            if isinstance(child, (QSpinBox, QDoubleSpinBox)):
                add(child.suffix())
        return " ".join(parts)

    def _filter_settings(self, text: str) -> None:
        tokens = [token for token in text.strip().casefold().split() if token]
        matched_any = False
        for panel, rows in self._panels:
            panel_matched = False
            for widget, keywords in rows:
                ok = not tokens or all(token in keywords for token in tokens)
                widget.setVisible(ok)
                if ok:
                    panel_matched = True
            if tokens:
                panel.setVisible(panel_matched)
                if panel_matched:
                    # 搜索结果直接展开，省得再点一次
                    panel.set_expanded(True, animate=False)
            else:
                panel.setVisible(True)
                panel.set_expanded(self._expanded_state.get(panel, True), animate=False)
            matched_any = matched_any or panel_matched
        self.no_match.setVisible(bool(tokens) and not matched_any)
        if tokens:
            self.scroll.verticalScrollBar().setValue(0)

    # ---------------------------------------------------------------- 外观
    def _build_appearance_panel(self) -> QWidget:
        frame, layout = _panel("外观与语言", "深色适合夜里追番，浅色适合白天；切换后立即生效")

        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(180)
        for code, name in i18n.available():
            self.language_combo.addItem(name, code)
        index = self.language_combo.findData(self.ctx.settings.language)
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.currentIndexChanged.connect(self._apply_language)
        layout.addWidget(
            _field(
                "界面语言",
                "语言文件在 ttdm/locales 下，一个 json 对应一种语言，缺条目自动回退中文",
                self.language_combo,
            )
        )

        theme_row = QWidget()
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.setSpacing(8)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self.theme_buttons: dict[str, QPushButton] = {}
        for value, text in (("dark", "深色"), ("light", "浅色")):
            button = QPushButton(text)
            button.setObjectName("Episode")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumSize(88, 34)
            button.clicked.connect(lambda _checked=False, v=value: self._apply_theme(v))
            self._theme_group.addButton(button)
            theme_layout.addWidget(button)
            self.theme_buttons[value] = button
        theme_layout.addStretch(1)
        layout.addWidget(_field("配色", "浅色下同样的配色会换成浅底深字", theme_row))

        accent_row = QWidget()
        accent_layout = QHBoxLayout(accent_row)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(8)
        self.accent_buttons: dict[str, QPushButton] = {}
        for value in theme.PRESET_ACCENTS:
            swatch = QPushButton()
            swatch.setFixedSize(26, 26)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(value)
            swatch.clicked.connect(lambda _=False, color=value: self._apply_accent(color))
            accent_layout.addWidget(swatch)
            self.accent_buttons[value] = swatch
        custom = make_button("自定义…", height=30)
        custom.clicked.connect(self._pick_accent)
        accent_layout.addWidget(custom)
        reset = make_button("恢复默认", height=30)
        reset.clicked.connect(lambda: self._apply_accent(""))
        accent_layout.addWidget(reset)
        accent_layout.addStretch(1)
        layout.addWidget(
            _field(
                "强调色",
                "只改当前这套配色（深色、浅色各记一个），按钮、进度条、选中态都会跟着换",
                accent_row,
            )
        )
        self._refresh_accent_buttons()

        self.motion_check = CheckToggle("减少界面动效（低配机器更顺滑）")
        self.motion_check.setChecked(self.ctx.settings.reduce_motion)
        self.motion_check.toggled.connect(self._apply_checks)
        layout.addWidget(self.motion_check)

        self.resume_check = CheckToggle("打开作品详情后自动继续上次看到的位置")
        self.resume_check.setChecked(self.ctx.settings.resume_on_open)
        self.resume_check.toggled.connect(self._apply_checks)
        layout.addWidget(self.resume_check)

        self.debug_check = CheckToggle("调试模式：隐藏番剧名称与封面")
        self.debug_check.setChecked(self.ctx.settings.debug_blank)
        self.debug_check.toggled.connect(self._apply_checks)
        layout.addWidget(self.debug_check)
        debug_hint = QLabel(
            "开启后所有条目只显示「番剧 01」这类别名和空封面，不再请求封面图片；"
            "「葬送的芙莉莲」保持正常，方便对照排查显示异常的条目。"
        )
        debug_hint.setObjectName("Muted")
        debug_hint.setWordWrap(True)
        layout.addWidget(debug_hint)

        indented = QWidget()
        indented_layout = QVBoxLayout(indented)
        indented_layout.setContentsMargins(22, 0, 0, 0)
        indented_layout.setSpacing(4)
        self.keep_search_check = CheckToggle("调试模式下搜索结果保持正常显示")
        self.keep_search_check.setChecked(self.ctx.settings.debug_keep_search)
        self.keep_search_check.toggled.connect(self._apply_checks)
        indented_layout.addWidget(self.keep_search_check)
        keep_hint = QLabel(
            "搜索页是排查坏条目的入口，因此默认放行；关掉后搜索结果也会被替换成别名与空封面。"
        )
        keep_hint.setObjectName("Faint")
        keep_hint.setWordWrap(True)
        indented_layout.addWidget(keep_hint)
        layout.addWidget(indented)
        return frame

    # ---------------------------------------------------------------- 下载
    def _build_download_panel(self) -> QWidget:
        frame, layout = _panel("下载", "默认保存到程序目录下的 downloads 文件夹，每部作品单独建文件夹")

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(str(default_download_dir()))
        self.dir_edit.setText(self.ctx.settings.download_dir or "")
        self.dir_edit.editingFinished.connect(self._apply_dir)
        row_layout.addWidget(self.dir_edit, 1)
        browse = make_button("浏览", icon_name="folder", height=36)
        browse.clicked.connect(self._browse_dir)
        row_layout.addWidget(browse)
        reset = make_button("默认", height=36)
        reset.clicked.connect(self._reset_dir)
        row_layout.addWidget(reset)
        layout.addWidget(_field("下载目录", "留空表示默认目录", row, stacked=True))

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(4, 64)
        self.workers_spin.setSingleStep(4)
        self.workers_spin.setSuffix(tr(" 线程"))
        self.workers_spin.setValue(self.ctx.settings.download_workers)
        self.workers_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field("单集并发分片", "越大越快，也越容易被源站限速；24 是实测比较稳的值", self.workers_spin)
        )

        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 6)
        self.parallel_spin.setSuffix(tr(" 集"))
        self.parallel_spin.setValue(self.ctx.settings.download_parallel)
        self.parallel_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field("同时下载集数", "同时下载太多集会互相抢带宽，2 集通常最快", self.parallel_spin)
        )

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("{名字} {集数}")
        self.template_edit.setText(self.ctx.settings.filename_template)
        self.template_edit.editingFinished.connect(self._apply_template)
        layout.addWidget(
            _field(
                "文件名模板",
                "可用占位符：{名字} {集数} {集号} {总数} {线路}；留空等于 {集数}",
                self.template_edit,
                stacked=True,
            )
        )
        self.template_preview = QLabel("")
        self.template_preview.setObjectName("Faint")
        layout.addWidget(self.template_preview)
        return frame

    # ---------------------------------------------------------------- 分类
    def _build_categories_panel(self) -> QWidget:
        frame, layout = _panel(
            "导航与分类",
            "取消勾选的分类不会出现在左侧导航，搜索与详情不受影响",
        )
        grid = QWidget()
        grid_layout = QHBoxLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(22)
        self.category_checks: dict[int, CheckToggle] = {}
        current = set(self.ctx.settings.categories)
        for name, type_id in CATEGORIES:
            toggle = CheckToggle(name)
            toggle.setMinimumWidth(150)
            toggle.setChecked(type_id in current)
            toggle.toggled.connect(self._apply_categories)
            grid_layout.addWidget(toggle)
            self.category_checks[type_id] = toggle
        grid_layout.addStretch(1)
        layout.addWidget(grid)

        layout.addWidget(Divider())
        self.cache_categories_check = CheckToggle("分类片库首次访问后缓存，再次进入不重新加载")
        self.cache_categories_check.setChecked(self.ctx.settings.cache_categories)
        self.cache_categories_check.toggled.connect(self._apply_checks)
        layout.addWidget(self.cache_categories_check)
        cache_hint = QLabel(
            "缓存只放在内存里，翻页翻过的分类来回切换会瞬间打开；需要最新数据时点片库右上角的「刷新」。"
        )
        cache_hint.setObjectName("Muted")
        cache_hint.setWordWrap(True)
        layout.addWidget(cache_hint)
        return frame

    # ---------------------------------------------------------------- 播放
    def _build_playback_panel(self) -> QWidget:
        frame, layout = _panel("播放", "本地代理会预先并发拉取后面的分片，拖动进度条基本等于读本地文件")
        self.buffer_spin = QSpinBox()
        self.buffer_spin.setRange(8, 64)
        self.buffer_spin.setSingleStep(4)
        self.buffer_spin.setSuffix(tr(" 线程"))
        self.buffer_spin.setValue(self.ctx.settings.buffer_workers)
        self.buffer_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field("播放预载并发", "播放时同时拉取的分片数，32 以上起播更快", self.buffer_spin)
        )

        self.auto_next = CheckToggle("播完自动播放下一集")
        self.auto_next.setChecked(self.ctx.settings.auto_next)
        self.auto_next.toggled.connect(self._apply_checks)
        layout.addWidget(self.auto_next)

        self.preload_count_spin = QSpinBox()
        self.preload_count_spin.setRange(1, 6)
        self.preload_count_spin.setSuffix(tr(" 集"))
        self.preload_count_spin.setValue(self.ctx.settings.preload_count)
        self.preload_count_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field(
                "预加载集数",
                "播放页的剧集列表里可以给后面的剧集点「+」提前拉取；这里是最多能选几集",
                self.preload_count_spin,
            )
        )

        self.preload_percent_spin = QSpinBox()
        self.preload_percent_spin.setRange(5, 50)
        self.preload_percent_spin.setSingleStep(5)
        self.preload_percent_spin.setSuffix(" %")
        self.preload_percent_spin.setValue(self.ctx.settings.preload_percent)
        self.preload_percent_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field(
                "预加载比例",
                "每集只预加载开头这么多分片；只占几个连接，不影响正在播放的那一集",
                self.preload_percent_spin,
            )
        )

        self.remember = CheckToggle("记住观看进度并显示在首页")
        self.remember.setChecked(self.ctx.settings.remember_position)
        self.remember.toggled.connect(self._apply_checks)
        layout.addWidget(self.remember)

        volume_row = QWidget()
        volume_layout = QHBoxLayout(volume_row)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(10)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(220)
        self.volume_slider.setValue(self.ctx.settings.volume)
        self.volume_value = QLabel("%d%%" % self.ctx.settings.volume)
        self.volume_value.setObjectName("Muted")
        self.volume_value.setFixedWidth(46)
        self.volume_slider.valueChanged.connect(self._apply_volume)
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_value)
        layout.addWidget(_field("默认音量", "进入播放页时应用的音量", volume_row))

        self.speed_combo = QComboBox()
        for label, value in (("0.75x", 0.75), ("1.0x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("1.75x", 1.75), ("2.0x", 2.0)):
            self.speed_combo.addItem(label, value)
        index = self.speed_combo.findData(round(float(self.ctx.settings.speed), 2))
        self.speed_combo.setCurrentIndex(index if index >= 0 else 1)
        self.speed_combo.currentIndexChanged.connect(self._apply_numbers)
        layout.addWidget(_field("默认播放速度", "播放页里可以随时切换", self.speed_combo))
        return frame

    # ---------------------------------------------------------------- 线路
    def _build_mirror_panel(self) -> QWidget:
        frame, layout = _panel(
            "站点线路",
            "启动时自动从 ttdm0.me 发布页收集镜像并测速，后台按间隔重新测速，避免某个域名失效",
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.mirror_combo = QComboBox()
        self.mirror_combo.setMinimumWidth(260)
        self.mirror_combo.currentIndexChanged.connect(self._apply_mirror)
        row_layout.addWidget(self.mirror_combo, 1)
        self.test_button = make_button("立即测速", icon_name="refresh", height=36)
        self.test_button.clicked.connect(self._refresh_mirrors)
        row_layout.addWidget(self.test_button)
        layout.addWidget(_field("当前线路", "选「自动」时始终使用测速最快、且可用的线路", row, stacked=True))

        self.mirror_list = QVBoxLayout()
        self.mirror_list.setSpacing(6)
        mirror_holder = QWidget()
        mirror_holder.setLayout(self.mirror_list)
        layout.addWidget(mirror_holder)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 60)
        self.refresh_spin.setSuffix(tr(" 分钟"))
        self.refresh_spin.setValue(self.ctx.settings.refresh_minutes)
        self.refresh_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field("后台测速间隔", "每隔这么久重新测速一次，自动切换到更快或仍存活的线路", self.refresh_spin)
        )
        return frame

    # ---------------------------------------------------------------- 存储
    def _build_storage_panel(self) -> QWidget:
        frame, layout = _panel("缓存与数据", "播放缓存在本机，重复观看同一集会直接读缓存")
        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(1024, 65536)
        self.cache_spin.setSingleStep(1024)
        self.cache_spin.setSuffix(" MB")
        self.cache_spin.setValue(self.ctx.settings.cache_limit_mb)
        self.cache_spin.valueChanged.connect(self._apply_numbers)
        layout.addWidget(
            _field("播放缓存上限", "超出后按最久未使用清理；一集 1080p 大约 230MB", self.cache_spin)
        )

        self.cache_size_label = QLabel("")
        self.cache_size_label.setObjectName("Muted")
        refresh_row = QWidget()
        refresh_layout = QHBoxLayout(refresh_row)
        refresh_layout.setContentsMargins(0, 0, 0, 0)
        refresh_layout.setSpacing(8)
        refresh_layout.addWidget(self.cache_size_label, 1)
        open_cache = make_button("打开缓存目录", icon_name="folder", height=34)
        open_cache.clicked.connect(lambda: self.shell.open_in_explorer(self.ctx.proxy.cache_root or Path(".")))
        refresh_layout.addWidget(open_cache)
        clear_cache = make_button("清空缓存", height=34)
        clear_cache.clicked.connect(self._clear_cache)
        refresh_layout.addWidget(clear_cache)
        layout.addWidget(refresh_row)

        layout.addWidget(Divider())
        clear_history = make_button("清空观看记录", height=34)
        clear_history.clicked.connect(self._clear_history)
        layout.addWidget(
            _field("观看记录", "清空后首页的「继续观看」会一起消失", clear_history)
        )
        return frame

    # ---------------------------------------------------------------- 重置
    def _build_reset_panel(self) -> QWidget:
        frame, layout = _panel(
            "重置与修复",
            "配置写坏或界面异常时，从轻到重依次尝试；重置只影响本机数据，不会删除已下载的视频",
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        reset_ui = make_button("重置界面设置", height=34)
        reset_ui.clicked.connect(self._reset_settings)
        row_layout.addWidget(reset_ui)
        reset_records = make_button("清空观看记录", height=34)
        reset_records.clicked.connect(self._clear_history)
        row_layout.addWidget(reset_records)
        reset_downloads = make_button("清空下载记录", height=34)
        reset_downloads.clicked.connect(self._clear_downloads)
        row_layout.addWidget(reset_downloads)
        row_layout.addStretch(1)
        layout.addWidget(row)

        onboarding = make_button("重新查看免责声明与小贴士", height=34)
        onboarding.clicked.connect(self.shell.show_onboarding)
        layout.addWidget(
            _field(
                "新手指引",
                "再次阅读启动时的免责声明与小贴士，确认后不会影响现有设置与记录",
                onboarding,
            )
        )

        layout.addWidget(Divider())
        danger = make_button("清空全部数据并恢复默认", height=34)
        danger.setObjectName("Danger")
        danger.clicked.connect(self._reset_all)
        layout.addWidget(
            _field("彻底重置", "设置、观看记录、下载记录、线路缓存全部清空，适合界面到处不对劲时", danger)
        )
        return frame

    # ---------------------------------------------------------------- 关于
    def _build_about_panel(self) -> QWidget:
        frame, layout = _panel("关于")
        info = QLabel(
            tr(
                "{app} {version} · 个人使用的小工具\n数据目录：{data}\n配置文件：{config}",
                app=APP_TITLE,
                version=APP_VERSION,
                data=self.ctx.store.path.parent,
                config=self.ctx.store.path,
            )
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(info)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        open_data = make_button("打开数据目录", icon_name="folder", height=34)
        open_data.clicked.connect(lambda: self.shell.open_in_explorer(self.ctx.store.path.parent))
        row_layout.addWidget(open_data)
        row_layout.addStretch(1)
        reset = make_button("恢复默认设置", height=34)
        reset.clicked.connect(self._reset_settings)
        row_layout.addWidget(reset)
        layout.addWidget(row)
        return frame

    # ---------------------------------------------------------------- 应用
    def _store(self) -> Callable[[], None]:
        return self.ctx.save

    def _apply_numbers(self) -> None:
        if self._loading:
            return
        settings = self.ctx.settings
        settings.download_workers = self.workers_spin.value()
        settings.download_parallel = self.parallel_spin.value()
        settings.buffer_workers = self.buffer_spin.value()
        settings.refresh_minutes = self.refresh_spin.value()
        settings.cache_limit_mb = self.cache_spin.value()
        settings.preload_count = self.preload_count_spin.value()
        settings.preload_percent = self.preload_percent_spin.value()
        settings.speed = float(self.speed_combo.currentData() or 1.0)
        self.ctx.proxy.cache_limit = settings.cache_limit_mb * 1024 * 1024
        self.ctx.save()
        self.shell.apply_settings()

    def _apply_checks(self) -> None:
        if self._loading:
            return
        settings = self.ctx.settings
        settings.auto_next = self.auto_next.isChecked()
        settings.remember_position = self.remember.isChecked()
        settings.reduce_motion = self.motion_check.isChecked()
        settings.resume_on_open = self.resume_check.isChecked()
        debug_changed = settings.debug_blank != self.debug_check.isChecked()
        keep_changed = settings.debug_keep_search != self.keep_search_check.isChecked()
        cache_changed = settings.cache_categories != self.cache_categories_check.isChecked()
        settings.debug_blank = self.debug_check.isChecked()
        settings.debug_keep_search = self.keep_search_check.isChecked()
        settings.cache_categories = self.cache_categories_check.isChecked()
        self.ctx.save()
        self.shell.apply_settings()
        if debug_changed or keep_changed:
            self.shell.apply_debug_mode()
            if debug_changed:
                self.ctx.notify(
                    tr("调试模式已开启，名称与封面已隐藏")
                    if settings.debug_blank
                    else tr("调试模式已关闭"),
                    "check",
                    theme.GREEN,
                )
            else:
                self.ctx.notify(
                    tr("搜索结果保持正常显示")
                    if settings.debug_keep_search
                    else tr("搜索结果已改为别名与空封面"),
                    "check",
                    theme.GREEN,
                )
        if cache_changed:
            self.ctx.notify(
                tr("分类片库缓存已开启") if settings.cache_categories else tr("分类片库缓存已关闭"),
                "check",
                theme.GREEN,
            )

    def _apply_language(self) -> None:
        if self._loading:
            return
        code = self.language_combo.currentData() or i18n.DEFAULT_LANGUAGE
        if code == self.ctx.settings.language:
            return
        self.shell.apply_language(code)
        self.ctx.notify(tr("界面语言已切换"), "check", theme.GREEN)

    # ---------------------------------------------------------------- 强调色
    def _accent_style(self, color: str, selected: bool) -> str:
        border = theme.ON_ACCENT if selected else "rgba(255,255,255,45)"
        width = 2 if selected else 1
        return (
            "QPushButton { background-color: %s; border: %dpx solid %s; border-radius: 13px; }"
            "QPushButton:hover { border-color: %s; }"
            % (color, width, border, theme.TEXT)
        )

    def _refresh_accent_buttons(self) -> None:
        current = (theme.accent() or "").upper()
        for value, button in getattr(self, "accent_buttons", {}).items():
            selected = value.upper() == current
            button.setStyleSheet(self._accent_style(value, selected))

    def _apply_accent(self, color: str) -> None:
        if self._loading:
            return
        self.shell.apply_accent(color)
        self._refresh_accent_buttons()
        self.ctx.notify(
            tr("强调色已更新") if color else tr("强调色已恢复默认"),
            "check",
            theme.GREEN,
        )

    def _pick_accent(self) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog

        initial = QColor(theme.accent())
        chosen = QColorDialog.getColor(initial, self, tr("选择强调色"))
        if chosen.isValid():
            self._apply_accent(chosen.name())

    def _apply_theme(self, mode: str) -> None:
        if self._loading:
            return
        if mode == self.ctx.settings.theme:
            return
        self.ctx.settings.theme = mode
        self.ctx.save()
        self.shell.apply_theme_mode(mode)
        self.ctx.notify(
            tr("配色已切换为{mode}", mode=tr("深色") if mode == "dark" else tr("浅色")),
            "check",
            theme.GREEN,
        )

    def _apply_template(self) -> None:
        if self._loading:
            return
        text = self.template_edit.text().strip()
        self.ctx.settings.filename_template = text or "{集数}"
        self.ctx.save()
        self._refresh_template_preview()

    def _refresh_template_preview(self) -> None:
        from ...core.downloader import format_stem

        sample = format_stem(
            self.ctx.settings.filename_template,
            1,
            tr("第1集"),
            tr("葬送的芙莉莲 第二季"),
            10,
            tr("线路 hh"),
        )
        self.template_preview.setText(tr("示例：{name}.mp4", name=sample))

    def _apply_categories(self) -> None:
        if self._loading:
            return
        chosen = [type_id for type_id, toggle in self.category_checks.items() if toggle.isChecked()]
        if not chosen:
            # 至少保留一个分类，否则侧栏会空掉
            sender = self.sender()
            if isinstance(sender, CheckToggle):
                sender.blockSignals(True)
                sender.setChecked(True)
                sender.blockSignals(False)
            chosen = [type_id for type_id, toggle in self.category_checks.items() if toggle.isChecked()]
        self.ctx.settings.categories = [tid for _, tid in CATEGORIES if tid in set(chosen)]
        self.ctx.save()
        self.shell.apply_settings()

    def _apply_volume(self, value: int) -> None:
        self.volume_value.setText("%d%%" % value)
        if self._loading:
            return
        self.ctx.settings.volume = int(value)
        self.ctx.save()

    def _apply_dir(self) -> None:
        if self._loading:
            return
        text = self.dir_edit.text().strip()
        self.ctx.settings.download_dir = text
        self.ctx.save()
        self.dir_edit.setText(self.ctx.settings.download_dir)

    def _browse_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = str(self.ctx.download_root())
        chosen = QFileDialog.getExistingDirectory(self, "选择下载目录", start)
        if chosen:
            self.dir_edit.setText(chosen)
            self._apply_dir()
            self.ctx.notify("下载目录已更新", "check", theme.GREEN)

    def _reset_dir(self) -> None:
        self.dir_edit.setText("")
        self._apply_dir()

    def _apply_mirror(self) -> None:
        if self._loading:
            return
        data = self.mirror_combo.currentData()
        self.shell.set_mirror(data or "")

    def _refresh_mirrors(self) -> None:
        self.test_button.setEnabled(False)
        self.test_button.setText(tr("测速中…"))
        self.shell.refresh_mirrors(force=True, on_done=self._on_mirrors_done)

    def _on_mirrors_done(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText(tr("立即测速"))
        self._reload_mirrors()

    def _reload_mirrors(self) -> None:
        report = self.ctx.mirror
        current = self.mirror_combo.currentData()
        self._loading = True
        self.mirror_combo.clear()
        self.mirror_combo.addItem(tr("自动（最快线路）"), "")
        for base in self.ctx.store.mirrors[:12]:
            self.mirror_combo.addItem(base.replace("https://", "").rstrip("/"), base)
        index = self.mirror_combo.findData(current or "")
        self.mirror_combo.setCurrentIndex(max(0, index))
        self._loading = False

        while self.mirror_list.count():
            item = self.mirror_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if report is None:
            label = QLabel(tr("尚未测速"))
            label.setObjectName("Faint")
            self.mirror_list.addWidget(label)
            return
        best = report.base_url.replace("https://", "").rstrip("/")
        head = QLabel(
            tr(
                "最近一次测速：{best} · 延迟 {ms} ms · 存活 {alive} / {total} 个候选",
                best=best,
                ms=int(report.latency_ms or 0),
                alive=len(report.alive),
                total=report.checked,
            )
        )
        head.setObjectName("Muted")
        self.mirror_list.addWidget(head)
        for base, latency in report.alive[:12]:
            row = QLabel(
                "  · %s   %d ms" % (base.replace("https://", "").rstrip("/"), int(latency))
            )
            row.setObjectName("Muted")
            self.mirror_list.addWidget(row)

    def _clear_cache(self) -> None:
        root = self.ctx.proxy.cache_root
        if root is None:
            return
        try:
            for child in root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        except OSError:
            pass
        self._refresh_cache_size()
        self.ctx.notify("播放缓存已清空", "check", theme.GREEN)

    def _refresh_data_pages(self) -> None:
        for name in ("home", "downloads"):
            page = self.shell._pages.get(name)  # noqa: SLF001 - 同进程页面协调
            refresh = getattr(page, "on_show", None)
            if callable(refresh):
                try:
                    refresh()
                except TypeError:
                    pass

    def _clear_history(self) -> None:
        self.ctx.store.clear_history()
        self.ctx.save()
        self._refresh_data_pages()
        self.ctx.notify("观看记录已清空", "check", theme.GREEN)

    def _clear_downloads(self) -> None:
        self.ctx.store.clear_downloads()
        self.ctx.save()
        self._refresh_data_pages()
        self.ctx.notify("下载记录已清空", "check", theme.GREEN)

    def _confirm(self, title: str, message: str, confirm_text: str = "确定") -> bool:
        dialog = ConfirmDialog(self, title, message, confirm_text, danger=True)
        return dialog.exec() == ConfirmDialog.Accepted

    def _reset_settings(self) -> None:
        self.ctx.settings = self.ctx.store.reset_settings()
        self.ctx.save()
        self.shell.apply_settings()
        self.shell.apply_theme_mode(self.ctx.settings.theme)
        self._load_values()
        self._reload_mirrors()
        self.ctx.notify("已恢复默认设置", "check", theme.GREEN)

    def _reset_all(self) -> None:
        if not self._confirm(
            "清空全部数据",
            "设置、观看记录、下载记录与线路缓存都会被清空，已下载的视频文件不会删除。",
            "清空并恢复",
        ):
            return
        self.ctx.settings = self.ctx.store.reset_all()
        self.ctx.save()
        self.shell.apply_settings()
        self.shell.apply_theme_mode(self.ctx.settings.theme)
        self._load_values()
        self._reload_mirrors()
        self._refresh_data_pages()
        self.ctx.notify("已清空数据并恢复默认", "check", theme.GREEN)

    def _sync_suffixes(self) -> None:
        """单位跟着语言走（QSpinBox 的 suffix 不会被控件自动翻译覆盖）。"""
        self.workers_spin.setSuffix(tr(" 线程"))
        self.buffer_spin.setSuffix(tr(" 线程"))
        self.parallel_spin.setSuffix(tr(" 集"))
        self.preload_count_spin.setSuffix(tr(" 集"))
        self.refresh_spin.setSuffix(tr(" 分钟"))

    def _load_values(self) -> None:
        settings = self.ctx.settings
        self._loading = True
        self._sync_suffixes()
        button = self.theme_buttons.get(settings.theme)
        if button is not None:
            button.setChecked(True)
        index = self.language_combo.findData(settings.language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self.template_edit.setText(settings.filename_template)
        for type_id, toggle in self.category_checks.items():
            toggle.setChecked(type_id in set(settings.categories))
        self.resume_check.setChecked(settings.resume_on_open)
        self._refresh_template_preview()
        self.dir_edit.setText(settings.download_dir or "")
        self.workers_spin.setValue(settings.download_workers)
        self.parallel_spin.setValue(settings.download_parallel)
        self.buffer_spin.setValue(settings.buffer_workers)
        self.refresh_spin.setValue(settings.refresh_minutes)
        self.cache_spin.setValue(settings.cache_limit_mb)
        self.preload_count_spin.setValue(settings.preload_count)
        self.preload_percent_spin.setValue(settings.preload_percent)
        self.auto_next.setChecked(settings.auto_next)
        self.remember.setChecked(settings.remember_position)
        self.motion_check.setChecked(settings.reduce_motion)
        self.debug_check.setChecked(settings.debug_blank)
        self.keep_search_check.setChecked(settings.debug_keep_search)
        self.cache_categories_check.setChecked(settings.cache_categories)
        self.volume_slider.setValue(settings.volume)
        index = self.speed_combo.findData(round(float(settings.speed), 2))
        self.speed_combo.setCurrentIndex(index if index >= 0 else 1)
        self._loading = False

    def _refresh_cache_size(self) -> None:
        root = self.ctx.proxy.cache_root
        total = 0
        if root is not None and root.exists():
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        self.cache_size_label.setText(tr("当前占用 {size}", size=fmt_size(total)))

    def refresh_theme(self) -> None:
        button = self.theme_buttons.get(self.ctx.settings.theme)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._refresh_accent_buttons()
        self._reload_mirrors()
        self._refresh_cache_size()

    def on_show(self, **kwargs) -> None:
        self._load_values()
        self._reload_mirrors()
        self._refresh_cache_size()
        QTimer.singleShot(60, self._refresh_cache_size)
