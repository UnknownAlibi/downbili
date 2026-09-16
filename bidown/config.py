"""路径、常量与默认设置。

注意：BASE_DIR 必须指向「用户可见的项目/程序目录」而不是本文件所在目录，
否则 settings.json、download/、crash.log 会被写进 bidown/ 包里。
- 打包后：exe 同级目录
- 开发时：项目根目录（bidown/ 的上一级）
"""

import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)).resolve()


SETTINGS_PATH = BASE_DIR / "settings.json"


DEFAULT_DOWNLOAD_DIR = BASE_DIR / "download"


HISTORY_PATH = DEFAULT_DOWNLOAD_DIR / "history.json"


CRASH_LOG_PATH = BASE_DIR / "crash.log"


RUNTIME_LOG_PATH = DEFAULT_DOWNLOAD_DIR / "runtime.log"


FFMPEG_EXE = Path()


def resolve_ffmpeg_path():
    """优先使用 exe 同级 ffmpeg.exe，其次打包内置。"""
    global FFMPEG_EXE
    exe_level = BASE_DIR / "ffmpeg.exe"
    if exe_level.exists():
        FFMPEG_EXE = exe_level
        return
    bundled = RESOURCE_DIR / "ffmpeg.exe"
    if bundled.exists():
        FFMPEG_EXE = bundled
        return
    FFMPEG_EXE = Path()


resolve_ffmpeg_path()


BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"


BILIBILI_PLAYURL_API = "https://api.bilibili.com/x/player/playurl"


BILIBILI_NAV_API = "https://api.bilibili.com/x/web-interface/nav"


BILIBILI_QRCODE_GENERATE_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"


BILIBILI_QRCODE_POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"


BILIBILI_DM_LIST_API = "https://api.bilibili.com/x/v1/dm/list.so"


QUALITY_LABELS = {
    "best": "最高画质（推荐）",
    "2160": "4K 2160P",
    "1440": "2K 1440P",
    "1080": "1080P",
    "720": "720P",
    "480": "480P",
    "360": "360P",
    "audio": "仅音频",
}


QUALITY_QN = {
    "2160": 120,
    "1440": 116,
    "1080": 80,
    "720": 64,
    "480": 32,
    "360": 16,
}


CODEC_LABELS = {
    "auto": "自动",
    "h264": "H.264 优先",
    "hevc": "HEVC 优先",
    "av1": "AV1 优先",
}


AUDIO_QUALITY_LABELS = {
    "auto": "自动",
    "high": "高（>=192k）",
    "medium": "中（>=128k）",
    "low": "低（任意）",
}


# B站错误码分类
BILI_ERROR_CATEGORIES = [
    (-101, "账号未登录", "请配置 Cookie 后重试（推荐扫码登录）。"),
    (-352, "风控拦截", "请求被 B站风控，请稍后再试或配置 Cookie。"),
    (-404, "视频不存在", "视频可能已被删除或 BV 号错误。"),
    (-403, "权限不足", "无权访问该视频，可能需要大会员或登录。"),
    (-509, "频率限制", "请求过于频繁，请稍后再试。"),
    (-616, "弹幕不存在", "该分 P 没有弹幕。"),
    (-701, "地区限制", "该视频在你所在地区不可访问，请使用代理。"),
    (-799, "会员限制", "需要大会员才能观看，请配置大会员 Cookie。"),
]


DEFAULT_SETTINGS = {
    "download_dir": str(DEFAULT_DOWNLOAD_DIR),
    "quality": "best",
    "custom_format": "",
    "cookie_mode": "none",
    "cookie_file": "",
    "proxy": "",
    "filename_template": "%(title).180B [%(id)s].%(ext)s",
    "fragment_threads": 4,
    "concurrent_downloads": 1,
    "codec_preference": "auto",
    "audio_quality": "auto",
    "download_thumbnail": False,
    "download_subtitle": False,
    "download_danmaku": False,
    "fx_sakura": True,
    "fx_neon": True,
    "fx_sound": True,
}


def ffmpeg_path():
    """当前可用的 ffmpeg 路径（未探测到时返回空 Path）。"""
    return FFMPEG_EXE


def has_ffmpeg():
    """是否探测到可用的 ffmpeg。"""
    return FFMPEG_EXE.exists()
