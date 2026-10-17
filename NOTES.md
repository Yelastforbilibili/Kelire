# TTDM 动漫下载器 —— 技术与接口笔记

> 本文件记录站点逆向结论与关键实现决策，供开发与后续维护查阅。原站目录中的源码无参考价值。

## 1. 站点与镜像

- 镜像发布页：`https://www.ttdm0.me/`，页面内散落各镜像域名，形如 `www.ttdm.me` / `www.ttdm14.me` / `www.ttdm8.me`。
- 可用镜像集合：正则 `https?://(?:www\.)?ttdm\d*\.me` 提取，忽略广告域名（88kq.net、doumei.cc、juwu.tv 等）。
- 启动时抓取镜像列表 → 并发探测首页延迟 → 取最快可用者作为 base_url；后台每 5 分钟复测更新。
- 慢/死站探测：GET `/` 超时 6s，读回首页特征（含 `module-poster-item` 或标题含「天天动漫」）。

## 2. 反爬（安全校验）

- 部分页面（如详情页）首次请求返回 966 字节的 `Security Check` 页，内含：

  `eval([...数字数组...].map(function(c){return String.fromCharCode(c^13)})...)`

- 解法：取数组与异或 key（当前 13），还原 JS 文本，正则取 `document.cookie="k=v;path=/;max-age=604800"`，
  把 `k=v` 加入请求 Cookie 后重试。当前值：`tt_sec_verify=c9a8f2e1d7b3`。
- 直接在请求头里带 `cookie: showBtn=true; tt_sec_verify=...` 即可，无需 Session 预热。

## 3. 页面结构与正则

- 首页：`<h2 class="module-title">正在热播</h2>` 等分区，分区标题含 `中国动漫\nguoman` 形式（小写拼音在 `<small>`）。
  卡片：`<a href="/vod/{id}/" title="名字" class="module-poster-item module-item">`，
  内含 `.module-item-note`（集数/更新）、`img` 的 `data-original`（封面，`static.ttfile.top`）。
  首页分区：正在热播(12)、中国动漫(18)、日本动漫(18)、欧美动漫(18)、里番动漫(18)、其他动漫(18)、动画电影(18)。
- 导航分类：`/vod-type/1/` 中国动漫、`/vod-type/2/` 日本动漫、`/vod-type/3/` 欧美动漫、`/vod-type/6/` 里番动漫、`/vod-type/4/` 其他动漫、`/vod-type/5/` 动画电影。
- 搜索：`GET /vod-search/-------------/?wd={urlencode(关键词)}`，结果同样是 `a.module-poster-item`，卡片文本含 年份/地区/类型。
- 详情：`GET /vod/{id}/`
  - 标题 `<h1>`；封面 `img.lazy.lazyload` 的 `data-original`。
  - `.module-info-item` 行：别名、导演、主演、语言/字幕、更新、集数。
  - 简介：`.module-info-introduction-content`。
  - 年份/地区标签：`.module-info-tag-link`；分类链接 `/vod-type/{n}/`，标签链接 `/vod-show/...`。
  - 线路 Tab：`.module-tab-item` 带 `data-sid="1"`、`data-show="线路 hh"`、`<small>集数</small>`。
  - 剧集：`.module-play-list-content` 按 Tab 顺序各一块，里面 `a.module-play-list-link`，`href="/vod-play/{vid}-{sid}-{n}/"`，`title="播放XX第N集"`。
- 播放：`GET /vod-play/{vid}-{sid}-{n}/?iframe=1`（必须带 `?iframe=1`，否则拿到 46KB 外壳页）返回 2177 字节播放器页，内含：

  `var player_data={"url":"https:\/\/play.hhuus.com\/play\/erkk3ppa\/index.m3u8","url_next":"...","from":"hhm3u8","id":"17566","sid":1,"nid":1,"vod_data":{...}}`

- 外壳页 `/vod-play/{vid}-{sid}-{n}/` 内嵌 `iframe src="/vod-play/{vid}-{sid}-{n}/?iframe=1"`。

## 4. 视频流（HLS）

- m3u8 主播放列表直接给出媒体列表（320 段/集），示例域名 `play.hhuus.com`；分片域名 `p.hhwenjian.com:65`。
- 加密：`#EXT-X-KEY:METHOD=AES-128,URI="enc.key",IV=0x00000000000000000000000000000000`，
  key 相对于播放列表地址（`https://play.hhuus.com/play/erkk3ppa/enc.key`，16 字节裸 key），
  用 AES-128-CBC 解密分片，IV 缺省取序号大端 16 字节。
- 速度实测（单集 320 段、约 230MB、平均码率 ~1.6Mbps）：
  - 4 并发 300 KB/s、16 并发 ~600 KB/s、32 并发 ~1.1 MB/s、48 并发 ~730 KB/s
  - 结论：单连接被限速，靠并发堆吞吐；下载/缓冲并发取 24–32。
- 因此「卡」的根因就是原站单线程串行拉分片；本地 HLS 代理用多线程预取+磁盘缓存把限制去掉。

## 5. 架构决策

- GUI：PySide6 6.12 Widgets + 自绘（无 QML），Windows 专用，启用高 DPI。
- 播放：本地 HLS 代理（`127.0.0.1`，改写 playlist 为本地分片、Python 侧 AES 解密、后台多线程预取、磁盘缓存）
  + `QMediaPlayer`（PySide6 自带 `ffmpegmediaplugin.dll`）+ 自绘控制条。备选：外部 mpv/VLC。
- 下载：`yt-dlp`，`concurrent_fragment_downloads` 24–32，`ffmpeg_location` 取 `imageio-ffmpeg` 自带 ffmpeg.exe（系统无 ffmpeg）。
- 图片：线程池异步下载 + 内存/磁盘缓存（`%LOCALAPPDATA%\TianTianAnime\image_cache`），Pillow 缩放与模糊背景。
- 配置：`%LOCALAPPDATA%\TianTianAnime\settings.json`；默认下载目录 `<程序根>\downloads\<动漫名>\`。
- 设计语言：深墨底 `#0B0E14`→`#0E1218` 渐变、卡片 `#14181F`、描边 rgba(255,255,255,.07)、
  文字 `#EDEFF3`/`#8B93A2`、主强调 `#E2404F`（克制的播放红，仅 CTA 与进度）、评分用暖金 `#E8B15C`。
  圆角 16、8pt 间距体系、无 emoji、线性图标自绘。

## 6. 两个值得记住的坑

### 6.1 `setParent(None)` 会闪出小窗口

清空卡片时如果写 `card.setParent(None)`，卡片会真的变成顶层窗口（`isWindow()` 为真）：
它在下一次事件循环才被 `deleteLater` 回收，Windows 合成器会把这几个窗口画出来，
表现就是「换页面 / 切设置的一瞬间弹出好几个小窗户，然后立刻消失」。
首页「继续观看」每次 `on_show` 都重建一次卡片，所以最容易被看到。

正确写法是**只隐藏不挪父级**，让 `deleteLater` 在原有父级下销毁：

```python
widget.hide()
widget.deleteLater()
```

实测：修复前 `QApplication.topLevelWidgets()` 会多出 5 个 166x597 的 `PosterCard`，
修复后为 0。

### 6.2 多语言的落地方式

- 语言文件 `ttdm/locales/<code>.json`，**键是中文原文**，值是译文；缺条目回退中文。
- 静态文案不用改代码：`QApplication` 上挂一个事件过滤器，控件 `Show` 时查表替换
  （`QLineEdit` 只翻 placeholder，绝不翻用户输入）。
- 页数、集数、通知这类运行时拼接的文案用 `tr("…{n}…", n=…)`；`Toast`、`BusyOverlay`、
  播放页的 `_show_busy/_show_error` 在收口处统一 `tr()` 一次，省掉几十处调用点。
- 回到中文时用「译文 → 中文」的反查表还原已经翻译过的控件；只有在当前语言就是中文时才启用，
  否则英文界面会被自己反查回中文。

## 7. 拖动进度条卡顿的两个原因（都要治）

1. **预取任务排在播放器请求前面**：原来是 `ThreadPoolExecutor` FIFO，起播先铺 32 个预取，
   拖动时 `get(目标段)` 提交到队尾，要等前面 32 个跑完。现在 `HlsSession` 改成
   「优先队列 + 几个调度线程」：`get()` 把目标段标成 pinned 且优先级 0，`bump()` 还会把
   距离播放点太远、还没开始的预取任务直接丢弃，带宽立刻集中到新位置。
2. **卡顿恢复把 seek 顶回原处**：原来一旦位置 9 秒没动，就按「当前 position」重连本地清单——
   而 seek 期间 position 还是旧值，于是播放器被拉回原处，看起来就是「拖过去没反应」。
   现在拖动期间单独判断：先催 `setPosition`（最多两次），还不行才带着**目标位置**重连，
   普通卡顿恢复不会在 seek 未落位时触发。

另一个坑：失败的 future 会留在 `_futures` 里被反复复用，于是某一段网络抖动之后**永久 502**。
新调度器里任务失败会从表里删掉，下一次请求重新排队。

实测：4 秒起播后拖到 92%（冷缓存），0.2 秒跳到目标、1.3 秒后画面继续走。

## 8. 页面切换为什么换成截图淡出

`QGraphicsOpacityEffect` 挂在整个页面上会让整棵控件树走离屏渲染，首页有几十张卡片时
每帧都要重绘，观感就是掉帧。现在切页前把旧页面 `grab()` 成一张位图，盖在新页面上做
200ms 透明度动画：一帧只合成一张图，新页面的子控件完全不参与重绘。内容加载的
`fade_in()` 也改成盖一层纯色蒙版，不再对整块内容做不透明度效果。

## 9. 浅色主题的强调色

深色下的播放红（`#E2404F`）放在白底上非常刺眼，浅色主题单独换成靛蓝
`#3D5BD9`（hover `#4F6CE8`、dark `#2C41A6`），导航选中、主按钮、进度条、勾选框
全都走 `ACCENT` 令牌，所以一次改完；播放页覆盖层始终是深色玻璃，红/靛蓝都协调。

## 10. 缓冲条与「拖回开头」的三个坑

- **缓冲条数据**：`HlsSession._ready` 记住已经落到内存/磁盘的分片下标，`buffer_ranges()`
  把相邻的下标并成时间段（秒），播放器按播放时长换成比例画在进度条下面。不额外发请求。
- **坑一：超时直接 502**。`get()` 以前 60 秒拿不到就抛 `TimeoutError`，代理回 502，
  ffmpeg 对中间分片的错误往往直接重开整个流 → 看起来就是「回到开头」。现在 `get()`
  会一直等到分片真正下来（最多三轮、总时长上限 180 秒），并把该分片重新排到队首。
- **坑二：播放器吞掉 seek**。播放器还在 Loading/Stalled 时 `setPosition()` 会被忽略，
  之后从 0 开始播。现在 `_seek_to()` 遇到未就绪状态会把目标写进 `_pending_position`，
  等 `durationChanged` 再跳。
- **坑三：卡顿恢复反噬**。普通卡顿恢复是"按当前位置重连"，如果 seek 还没落位，
  就会把人拽回原处。现在 seek 期间走单独分支：先重发 `setPosition`（最多 3 次），
  仍然失败才**带着目标位置**重连；同时 `_on_position` 里检测「目标很远但位置回到 0」
  并自动补跳一次。

## 11. 折叠面板与更多动效

- 设置面板的折叠用 `QPropertyAnimation` 动 `content.maximumHeight`（0 ↔ sizeHint），
  箭头是一个自绘 `_Chevron`，用 `QVariantAnimation` 转 -90°；「减少动效」时两个动画
  都直接跳到终态。搜索结果过滤时命中的面板强制展开、清空后恢复用户之前的展开状态。
- 切主题/换强调色的交叉淡出直接复用切页那套 `SnapshotFade`（先 `self.grab()`）。
- 剧集抽屉开合只动 `pos`（从右侧滑入 30px），不重排里面的剧集行，掉帧最少。
- 强调色派生规则：hover = 与白色混合 14%（浅色 10%），dark = 与黑色混合 16%（浅色 22%）；
  导航选中底色用 38/20 的透明度；亮度过高时 `ON_ACCENT` 自动改成深色字。



