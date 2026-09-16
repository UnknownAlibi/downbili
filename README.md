# downbili

一个精简的视频下载工具。主入口是 `gui_download_qt.py`，当前版本只保留下载功能，不再加载动漫资源站、数据库或播放器模块。

## 启动

```powershell
py gui_download_qt.py
```

也可以双击 `启动下载器.bat`。

## 功能

- 支持输入 Bilibili 链接或 BV 号
- 支持 yt-dlp 能识别的其他常见视频页面链接
- 粘贴链接后自动解析预览，显示标题、封面、作者、时长、播放/点赞和分 P 数量
- 显示可用格式表，包括格式 ID、类型、分辨率、帧率、编码和估算大小
- 可在格式表中选择一行，作为本次下载格式
- 支持批量下载、下载目录、清晰度、代理、文件名模板
- 支持 cookies.txt，也可以尝试读取 Chrome / Edge / Firefox Cookie
- B 站无 Cookie 遇到 412 时，会自动尝试普通公开视频兜底下载
- 下载完成后会显示本地文件大小、时长、分辨率、帧率和音视频编码

## 项目结构

```text
gui_download_qt.py      启动入口（只负责单实例锁 + 打开主窗口）
bidown/
  config.py             路径、常量、默认设置
  utils.py              纯函数工具（输入拆分、文件名模板、数值格式化）
  urls.py               链接 / BV 号解析
  media.py              ffmpeg 媒体信息探测
  history.py            下载历史读写
  settings.py           settings.json 读写
  bilibili.py           B站 Web API（视频信息、播放地址、Cookie 会话、弹幕）
  net.py                HTTP 头、yt-dlp 选项、Cookie/代理注入
  errors.py             错误文案与 B站错误分类
  logs.py               崩溃日志 / 运行日志
  shell.py              用系统默认程序打开文件、目录
  mascot.py             看板娘绘制
  workers/              预览、下载、Cookie 检测、扫码登录、封面加载线程
  ui/                   主窗口、扫码对话框、特效控件（粒子/发光/音效/拖拽输入）
sounds/                 音效
download/               下载产物与历史（已 gitignore）
```

入口文件名、`启动下载器.bat`、打包 spec 和测试导入路径都保持不变。

## 测试

```powershell
py -m pytest -q
```

`test_utils.py` 覆盖纯函数（链接解析、文件名模板、媒体信息解析等），
`test_smoke.py` 在离屏模式下构建主窗口与各 Worker，作为重构后的回归保障。

## 依赖

```powershell
py -m pip install -r requirements.txt
```

项目目录下的 `ffmpeg.exe` 会被自动用于合并音视频。

## B 站 Cookie

公开视频通常可以直接下载；如果遇到 412、登录限制、高清不可用、会员内容或仅音频下载，请配置 Cookie。

无 Cookie 的 B 站兜底下载只使用普通公开视频直链，通常只能拿到 720P/360P 单文件；更高清、仅音频、DASH 格式、会员内容等需要 Cookie 和 yt-dlp 正常解析。

推荐方式：

1. 在浏览器登录 B 站
2. 用浏览器扩展导出 `cookies.txt`
3. 在下载器里选择 `cookies.txt 文件`

也可以在下载器里选择读取 Chrome / Edge / Firefox Cookie，但浏览器正在运行或权限不足时可能失败。
