# 下载器后续开发执行文档

本文档用于交接给其他工具或开发代理继续执行。项目当前目标是做一个轻量、可靠、易用的桌面视频下载器，主入口为 `gui_download_qt.py`。

## 当前状态

完成度约 70%。

已完成：

- 项目已精简为纯下载器。
- 主程序：`gui_download_qt.py`
- 启动脚本：`启动下载器.bat`
- 依赖文件：`requirements.txt`
- 下载输出目录：`download/`
- 支持 Bilibili 链接、BV 号，以及 yt-dlp 支持的其他视频网页。
- 支持 `cookies.txt`、Chrome / Edge / Firefox 浏览器 Cookie。
- 支持代理、下载目录、文件名模板、清晰度选择。
- 粘贴链接后自动解析预览。
- 预览信息包括封面、标题、作者、时长、发布日期、播放量、点赞、分 P 数。
- 格式表展示格式 ID、类型、分辨率、FPS、编码、估算大小。
- 可选择格式并下载。
- 下载完成后展示本地文件大小、时长、分辨率、帧率、视频编码、音频编码。
- B 站无 Cookie 遇到 `HTTP 412` 时，会尝试普通公开视频兜底下载。

重要限制：

- 无 Cookie 的 B 站兜底模式通常只能稳定拿到 720P / 360P 单文件。
- 更高清、仅音频、会员内容、DASH 精确格式，需要 Cookie 并依赖 yt-dlp 正常解析。
- 当前没有完整下载历史、任务重试、暂停继续、字幕/弹幕/封面下载等成熟功能。

## 最近一轮修复（保持文档与实际一致）

- 媒体信息解析：旧正则几乎匹配不到分辨率/帧率/编码，导致下载完成后队列与历史里这些字段为空；已改为按行解析（`parse_media_info_text`），并有单元测试覆盖。
- B站下载策略：改为「无 Cookie → 公开接口兜底；有 Cookie 且 ffmpeg 可用 → 优先 yt-dlp，失败再回退兜底」，与本文档描述一致。只有 yt-dlp 路径支持 DASH 合并、封面、字幕、文件名模板。
- 文件名模板：B站兜底路径此前写死 `标题.mp4`，现已按「文件名模板」设置渲染（`render_filename_template`），命名风格与 yt-dlp 路径统一。
- 并发下载：`current_filename` 改为线程局部存储（`threading.local`），修复多任务并发时输出文件路径串号。
- 取消清理：只删除本次任务登记过的临时文件，不再扫描整个下载目录，避免误删用户自己放进去的文件。
- 封面加载：移到后台线程（`ThumbnailWorker`），不再在主线程做网络请求。
- 历史列表：填充期间关闭排序（避免行序错位）；批量下载期间不再逐条重建历史表（O(n²) 卡顿）。
- 其他修复：设置页「任务并发」重启后丢失；第二个实例启动时因 `QMessageBox` 早于 `QApplication` 而崩溃；看板娘气泡在进度回调中频繁跳动；粒子层隐藏后仍在后台重绘；手动解析可能触发两次。
- 依赖与打包：`requirements.txt` 补上 `browser-cookie3`（此前浏览器 Cookie 模式必然静默失败）；打包 spec 补上 `sounds/` 资源。

### 仍未处理的已知问题

- 兜底路径下选中的 `custom_format` 只用于反推清晰度 qn，不保证精确匹配用户所选格式。
- `custom_format` 全局持久化，切换链接后不会自动清空，可能带到不兼容的站点。
- `custom_format` 为 `300xx+302xx` 时若 Cookie 失效，会先走 yt-dlp 失败再兜底，多一次请求耗时。
- `runtime.log` / `crash.log` 无轮转，长期使用会持续增长。
- 队列中被「从队列删除」的任务仍计入进度条分母。
- 快捷键 `Space` / `Esc` 是全局绑定，焦点不在输入框时可能误触暂停/取消。

## 结构重构（2026-09）

原先把全部 4400 多行堆在 `gui_download_qt.py` 单文件里，已按职责拆分为 `bidown/` 包，
**只做代码原样搬迁，不改业务逻辑**：

```text
gui_download_qt.py          启动入口（单实例锁 + 打开主窗口 + 兼容旧导入路径）
bidown/config.py            路径、常量、默认设置、ffmpeg 探测
bidown/utils.py             纯函数：输入拆分、文件名模板、路径与数值格式化
bidown/urls.py              链接 / BV 号解析
bidown/media.py             ffmpeg 媒体信息探测
bidown/history.py           下载历史读写
bidown/settings.py          settings.json 读写
bidown/bilibili.py          B站 Web API：视频信息、播放地址、Cookie 会话、弹幕
bidown/net.py               HTTP 头、yt-dlp 静默 logger、Cookie/代理注入
bidown/errors.py            错误文案与 B站错误分类
bidown/logs.py              崩溃日志 / 运行日志
bidown/shell.py             用系统默认程序打开文件、目录
bidown/mascot.py            看板娘绘制
bidown/workers/             preview / download / cookie / qrlogin / thumbnail 线程
bidown/ui/                  main_window、qrlogin_dialog、effects（粒子、发光、音效、拖拽输入）
```

约定：

- 入口文件名不变，`启动下载器.bat`、`downloader.spec`、`py gui_download_qt.py` 都照旧可用。
- `gui_download_qt.py` 继续 re-export 纯函数，旧的 `from gui_download_qt import extract_bvid` 写法不会失效。
- 跨模块不再直接导入 `FFMPEG_EXE` 全局变量，改用 `ffmpeg_path()` / `has_ffmpeg()`，避免拿到过期值。
- 测试：`test_utils.py`（纯函数）+ `test_smoke.py`（离屏构建主窗口与各 Worker）。

搬迁时的校验方式（可复用）：用 AST 提取重构前后每个顶层函数/类的源码，归一化后逐一比对，
确保「丢失 0 个、内容不一致 0 个」；再配合 `py -m compileall`、`pytest`、离屏冒烟测试。

## 保留文件

项目根目录当前应保留：

- `.gitignore`
- `README.md`
- `DEVELOPMENT_PLAN.md`
- `requirements.txt`
- `gui_download_qt.py`
- `bidown/`
- `test_utils.py`
- `test_smoke.py`
- `启动下载器.bat`
- `ffmpeg.exe`
- `icon.ico`
- `icon.png`
- `download.png`
- `folder.png`
- `sounds/`
- `download/`
- `settings.json` 可存在，但应继续被 `.gitignore` 忽略
- `_backup/gui_download_qt_single_file.py` 为重构前的单文件备份，确认重构无问题后可删除

不要重新引入旧模块：

- 动漫资源站相关代码
- 数据库编辑脚本
- mpv 播放器
- PySide / QML 播放器
- 旧 `qt6.py`
- Selenium 爬虫

## 执行优先级

### P0：保证稳定可用

目标：普通用户粘贴链接后能确认视频、选择格式、下载，并在失败时知道原因。

任务：

- [x] 修正所有明显 UI 卡顿点，预览线程和下载线程不能阻塞主窗口。
- [x] 下载中禁用会导致状态错乱的设置项。
- [x] 增加"打开下载目录"按钮。
- [x] 增加"打开已下载文件"按钮或双击输出路径打开文件。
- [x] 增加"复制错误信息"按钮或右键复制。
- [x] Cookie 文件不存在时，在开始下载前给明确提示。
- [x] 浏览器 Cookie 读取失败时，提示关闭浏览器或改用 `cookies.txt`。

验收：

- 输入 `BV1bK411W797?p=2` 能预览。
- 不配置 Cookie 时，能用 B 站兜底下载公开视频。
- 下载完成后能看到分辨率、帧率、编码、文件大小。
- 失败不会导致程序崩溃或按钮无法恢复。

测试命令：

```powershell
py -m py_compile gui_download_qt.py
py gui_download_qt.py
```

### P1：任务管理

目标：像一个真正下载器，而不是一次性脚本。

任务：

- [x] 增加下载历史文件，例如 `download/history.json`。
- [x] 历史记录字段建议：
  - title
  - url
  - output_path
  - status
  - created_at
  - finished_at
  - file_size
  - duration
  - resolution
  - fps
  - video_codec
  - audio_codec
  - error
- [x] 增加历史列表 UI。
- [x] 支持重新下载历史任务。
- [x] 支持打开文件所在目录。
- [x] 支持删除历史记录，不默认删除本地文件。
- [x] 支持失败任务一键重试。

验收：

- 下载完成后历史能持久保存。
- 重启程序后仍能看到历史。
- 历史里的文件路径不存在时，界面应显示"文件已移动或删除"。

### P1：B 站专属增强

目标：B 站体验明显强于普通 yt-dlp 包装器。

任务：

- [x] 分 P/合集选择界面。
- [x] 预览时展示所有分 P，而不是只预览第一个。
- [x] 支持选择下载单个分 P、多个分 P、全部分 P。
- [x] 支持下载封面。
- [x] 支持下载字幕。
- [x] 支持下载弹幕 XML。
- [x] Cookie 状态检测：
  - 是否已登录
  - 用户名
  - 是否拿到 `SESSDATA`
  - 是否能访问 wbi playurl
- [x] 对 B 站错误分类：
  - 412 风控
  - 需要登录
  - 会员限制
  - 地区限制
  - 视频不存在
  - 权限不足

验收：

- 多 P 链接能列出所有分 P。
- 用户可以只下载第 2 P。
- 配置有效 Cookie 后，能显示"Cookie 可用/已登录"。

### P1：格式选择增强

目标：格式选择清楚、准确、不会误导。

任务：

- [x] yt-dlp 成功解析时，展示推荐组合格式，例如 `bv*+ba/b`。
- [x] 对 B 站 DASH 格式做组合展示：
  - 视频流
  - 音频流
  - 组合后的预计大小
  - 需要 ffmpeg 合并
- [x] 支持编码偏好：
  - 自动
  - H.264 优先
  - HEVC 优先
  - AV1 优先
- [x] 支持音频质量偏好。
- [x] 格式表增加"推荐"标记。
- [x] 预览中区分：
  - 可直接下载
  - 需要 Cookie
  - 需要 ffmpeg 合并
  - 当前不可用

验收：

- 用户能看懂当前下载的是 720P、1080P、仅音频还是 DASH 合并。
- 选择格式后，下载设置里明确显示格式选择字符串。

### P2：暂停、继续、取消、重试

目标：队列控制更可靠。

任务：

- [x] 当前下载任务支持取消后清理未完成临时文件。
- [x] 失败任务支持重试。
- [x] 队列任务支持删除等待中的任务。
- [x] 暂停/继续可以先做"队列级暂停"，不要求中断单个 HTTP 流。
- [x] 若继续下载依赖 yt-dlp 的 `continuedl`，文档中写清楚限制。

> **continuedl 限制说明**：项目已启用 yt-dlp 的 `continuedl=True`，对未完成的 `.part` 文件可断点续传。
> 限制：
> - 仅对同一输出路径的 `.part` 文件有效；若文件名模板包含会变化的字段（如时间戳），续传会失效。
> - 取消后会清理 `.part`/`.ytdl`/`.temp` 临时文件，因此"取消后再下载"等同于重新下载，而非续传。
> - 队列级暂停不会中断当前 HTTP 流，只在当前任务完成后等待恢复。
> - B 站兜底接口（公开视频直链）不走 yt-dlp，不支持续传。

验收：

- 下载中取消后按钮状态恢复。
- 再次点击下载不会复用错误状态。

### P2：用户体验 polish

目标：让界面更像稳定工具。

任务：

- [x] 设置区整理为更紧凑的布局，减少垂直滚动。
- [x] 增加状态栏。
- [x] 增加下载完成系统通知，或至少弹出轻量提示。
- [x] 日志区域支持清空。
- [x] 表格支持复制单元格内容。
- [x] 输出路径过长时 UI 不撑爆布局。
- [x] Windows 下打开文件/目录使用 `os.startfile`，并捕获异常。

验收：

- 1280x720 窗口内主要功能都能看见。
- 长标题、长路径不会破坏布局。

### P3：打包

目标：交付普通用户可运行的 exe。

任务：

- [x] 重新创建 PyInstaller spec。
- [x] 确保包含：
  - `ffmpeg.exe`
  - `icon.ico`
  - `download.png`
  - `folder.png`
- [x] 打包后默认下载目录应位于 exe 同级 `download/` 或用户选择目录。
- [x] 打包后 Cookie、settings、history 不应写入临时目录。
- [x] 写一份打包说明。

> **打包说明**：
> 1. 安装 PyInstaller：`py -m pip install pyinstaller`
> 2. 确保项目根目录存在 `ffmpeg.exe`、`icon.ico`、`download.png`、`folder.png`。
> 3. 执行打包：`py -m PyInstaller downloader.spec`
> 4. 产物位于 `dist/Bilibili视频下载器/`，双击 `Bilibili视频下载器.exe` 运行。
> 5. 用户文件（`settings.json`、`download/`、`download/history.json`、`cookies.txt`）写入 exe 同级目录，不会进入临时解压目录。
> 6. 如需替换 `ffmpeg.exe`，直接覆盖 `dist/Bilibili视频下载器/ffmpeg.exe` 即可（程序优先读取 exe 同级 ffmpeg.exe）。
> 7. 打包模式为 onedir（非 onefile），启动更快且便于替换资源。

验收：

- 干净 Windows 环境可双击运行。
- 打包版能预览、下载、保存设置。

## 推荐实现细节

### 线程模型

保留两个线程方向：

- `PreviewWorker`：解析预览，不能下载大文件。
- `DownloadWorker`：执行下载，负责进度和下载完成后的媒体信息。

不要在主线程里做网络请求、下载、ffmpeg 探测。

### yt-dlp 策略

优先使用 yt-dlp：

- 通用站点全部走 yt-dlp。
- B 站有 Cookie 且 yt-dlp 可用时，也优先走 yt-dlp。
- B 站无 Cookie 且遇到 412，再走项目内置普通公开视频兜底。

### B 站兜底策略

兜底接口：

- `https://api.bilibili.com/x/web-interface/view`
- `https://api.bilibili.com/x/player/playurl`

兜底只承诺普通公开视频单文件直链。不要在 UI 里把暂未实现的 DASH 格式展示为“可直接下载”。

### 媒体信息

当前使用 `ffmpeg.exe -hide_banner -i file` 解析 stderr。后续更稳的方案：

- 如果可以新增 `ffprobe.exe`，改用 JSON 输出。
- 如果不新增可执行文件，继续用 ffmpeg 文本解析，但要容错。

建议字段：

- container
- duration
- bitrate
- width
- height
- fps
- video_codec
- audio_codec
- audio_sample_rate
- file_size

## 回归测试清单

每次改完至少跑：

```powershell
py -m py_compile gui_download_qt.py
py -c "import PyQt5, requests, yt_dlp; print('deps ok')"
```

手动测试：

- [ ] 启动程序
- [ ] 输入 `BV1bK411W797?p=2`
- [ ] 等待预览出现
- [ ] 确认封面、标题、格式表存在
- [ ] 选择一个格式
- [ ] 下载
- [ ] 确认下载完成
- [ ] 确认输出列展示分辨率、帧率、编码、大小
- [ ] 打开下载目录确认文件存在

Cookie 测试：

- [ ] 不配置 Cookie，确认公开视频兜底可用
- [ ] 配置无效 Cookie，确认错误提示明确
- [ ] 配置有效 Cookie，确认 yt-dlp 路径可用

异常测试：

- [ ] 空输入
- [ ] 错误链接
- [ ] 不存在 BV
- [ ] 网络断开
- [ ] 下载中取消
- [ ] 下载目录无权限

## 不建议做的事

- 不要重新引入旧动漫资源站模块。
- 不要重新引入 mpv 播放器，除非明确要做播放器功能。
- 不要把 Cookie 写进 git。
- 不要把下载产物写进 git。
- 不要为了兜底下载绕过登录/会员权限。
- 不要在主线程里做网络请求。

## 参考方向

- yt-dlp：格式选择、下载、站点适配。
- ffmpeg/ffprobe：媒体信息读取、音视频合并。
- 成熟下载器常见体验：链接抓取、预览确认、格式选择、队列、历史、错误分类。
