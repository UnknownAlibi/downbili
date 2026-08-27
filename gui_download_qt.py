"""Bilibili 视频下载器 - 二次元风格 GUI

支持 Bilibili 链接/BV 号，以及 yt-dlp 支持的其他视频页面。
功能：预览、格式选择、队列下载、历史记录、扫码登录、Cookie 检测、
封面/字幕/弹幕下载、暂停/继续/取消/重试、系统通知。
"""

import csv
import concurrent.futures
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp
from PyQt5.QtCore import QEasingCurve, QLockFile, QPropertyAnimation, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QKeySequence
from PyQt5.QtMultimedia import QSoundEffect
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ==================== 路径与常量 ====================

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
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


# ==================== 异常钩子 ====================

def write_crash_log(exc_type, exc_value, exc_tb, source="main"):
    """将未捕获异常写入 crash.log。"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n==== {ts} | source={source} ====\n")
            f.write(tb_text)
    except Exception:
        pass


def install_excepthooks():
    """安装全局异常钩子，避免静默闪退。"""
    def qt_hook(exc_type, exc_value, exc_tb):
        write_crash_log(exc_type, exc_value, exc_tb, source="excepthook")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = qt_hook

    def thread_hook(args):
        write_crash_log(args.exc_type, args.exc_value, args.exc_traceback,
                        source=f"thread:{args.thread.name}")
        sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


# ==================== 工具函数 ====================

def std_headers(referer="https://www.bilibili.com/"):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


class QuietYtdlpLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def apply_cookie_and_proxy_options(opts, settings):
    """为 yt-dlp 选项注入 Cookie 和代理。"""
    mode = settings.get("cookie_mode") or "none"
    if mode == "file":
        cookie_file = (settings.get("cookie_file") or "").strip()
        if cookie_file and Path(cookie_file).exists():
            opts["cookiefile"] = cookie_file
    elif mode in ("chrome", "edge", "firefox"):
        opts["cookiesfrombrowser"] = (mode,)
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    return opts


def write_runtime_log(msg):
    """将运行日志追加到 runtime.log（自动创建目录）。"""
    try:
        RUNTIME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RUNTIME_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def load_settings():
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged, None
    except Exception as exc:
        return dict(DEFAULT_SETTINGS), str(exc)
    return dict(DEFAULT_SETTINGS), None


def save_settings(settings):
    """保存设置，返回 (True, None) 或 (False, error_msg)。"""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True, None
    except Exception as exc:
        return False, str(exc)


def split_inputs(text):
    """把输入框文本拆成链接列表。"""
    if not text:
        return []
    parts = re.split(r"[\s,，]+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def extract_bvid(url):
    """从链接中提取 BV 号。"""
    m = re.search(r"(BV[0-9A-Za-z]{10,})", url)
    return m.group(1) if m else ""


def extract_aid(url):
    """从链接中提取 av 号（纯数字）。"""
    m = re.search(r"av(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_video_id(url):
    """从链接中提取视频 ID，返回 (类型, id) 或 (None, None)。"""
    bvid = extract_bvid(url)
    if bvid:
        return ("bvid", bvid)
    aid = extract_aid(url)
    if aid:
        return ("aid", aid)
    return (None, None)


def normalize_input(text):
    """规范化输入：BV/av 号补全为完整链接，其他原样返回。"""
    text = (text or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"BV[0-9A-Za-z]{10,}", text):
        return f"https://www.bilibili.com/video/{text}"
    if re.fullmatch(r"[aA][vV]\d+", text):
        return f"https://www.bilibili.com/video/{text.lower()}"
    return text


def is_bilibili_url(url):
    return "bilibili.com" in url or "b23.tv" in url or re.fullmatch(r"BV[0-9A-Za-z]{10,}", url) or re.fullmatch(r"[aA][vV]\d+", url)


def selected_page_number(url):
    """从 URL 中提取 ?p= 参数。"""
    try:
        qs = parse_qs(urlparse(url).query)
        p = qs.get("p", ["1"])[0]
        return int(p)
    except Exception:
        return 1


def sanitize_filename(name, max_len=120):
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name or "")
    return name[:max_len].strip() or "video"


def unique_path(path):
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(1, 10000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名: {path}")


def format_duration(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        return "-"
    if seconds <= 0:
        return "-"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_bytes(num):
    try:
        num = float(num)
    except Exception:
        return "-"
    if num <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def format_count(num):
    try:
        num = int(num)
    except Exception:
        return "-"
    if num >= 100000000:
        return f"{num / 100000000:.1f}亿"
    if num >= 10000:
        return f"{num / 10000:.1f}万"
    return str(num)


def format_upload_date(date_str):
    if not date_str or date_str == "-":
        return "-"
    s = str(date_str)
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def format_fps(fps):
    try:
        fps = float(fps)
    except Exception:
        return "-"
    if fps <= 0:
        return "-"
    return f"{fps:.0f}"


def parse_media_info_text(text):
    """解析 ffmpeg -i stderr 文本。"""
    info = {}
    if not text:
        return info
    m = re.search(r"Duration:\s*([\d:.]+)", text)
    if m:
        info["duration"] = m.group(1)
    m = re.search(r"bitrate:\s*(\d+)", text)
    if m:
        info["bitrate"] = int(m.group(1))
    video_match = re.search(
        r"Stream #\d+:\d+.*Video:\s*(\w+)[^,]*,\s*(\d+)x(\d+)[^,]*,?\s*([\d.]+)?\s*tbr?",
        text,
    )
    if video_match:
        info["video_codec"] = video_match.group(1)
        info["width"] = int(video_match.group(2))
        info["height"] = int(video_match.group(3))
        if video_match.group(4):
            info["fps"] = float(video_match.group(4))
    audio_match = re.search(r"Stream #\d+:\d+.*Audio:\s*(\w+)[^,]*,\s*(\d+)\s*Hz", text)
    if audio_match:
        info["audio_codec"] = audio_match.group(1)
        info["audio_sample_rate"] = int(audio_match.group(2))
    return info


def media_info_for_file(path):
    """用 ffmpeg -i 解析媒体信息。"""
    if not FFMPEG_EXE.exists() or not Path(path).exists():
        return {}
    try:
        proc = subprocess.run(
            [str(FFMPEG_EXE), "-hide_banner", "-i", str(path)],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = proc.stderr.decode("utf-8", errors="ignore")
        info = parse_media_info_text(text)
        info["file_size"] = Path(path).stat().st_size
        return info
    except Exception:
        return {}


def format_error(exc):
    msg = str(exc)
    if not msg:
        msg = exc.__class__.__name__
    return msg


def format_bili_error(exc):
    """格式化错误信息，对 B站错误做分类提示。"""
    base = format_error(exc)
    category = classify_bili_error(exc)
    if category:
        return f"[{category[0]}] {base}\n→ {category[1]}"
    return base


def extract_output_path(output_detail):
    """从输出详情文本中提取路径。"""
    if not output_detail:
        return ""
    lines = [l.strip() for l in output_detail.splitlines() if l.strip()]
    if not lines:
        return ""
    last = lines[-1]
    if Path(last).exists():
        return last
    for line in reversed(lines):
        if Path(line).exists():
            return line
    return last


def open_file_default(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        QMessageBox.warning(None, "打开失败", f"无法打开文件：{format_error(exc)}")


def open_path_in_explorer(path):
    try:
        p = Path(path)
        target = str(p.parent if p.is_file() else p)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        QMessageBox.warning(None, "打开失败", f"无法打开目录：{format_error(exc)}")


def generate_mascot_image(path, size=200):
    """绘制几何极简风看板娘 PNG：柔和色块、干净线条、现代扁平感。"""
    h = int(size * 1.25)
    pixmap = QPixmap(size, h)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    def ellipse(x, y, w, t_h):
        painter.drawEllipse(int(x), int(y), int(w), int(t_h))

    def line(x1, y1, x2, y2, color, width=2):
        pen = QPen(QColor(color))
        pen.setWidth(width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    try:
        cx, cy = size // 2, int(size * 0.58)
        face_r = int(size * 0.30)

        # 配色：低饱和珊瑚、奶油、柔灰
        hair = QColor(120, 113, 108)      # 暖灰褐
        skin = QColor(255, 237, 213)      # 奶油肤色
        blush = QColor(254, 202, 202, 160)
        accent = QColor(251, 146, 60)     # 珊瑚橙
        line_color = QColor(87, 83, 78)

        # 后发：大圆
        painter.setPen(Qt.NoPen)
        painter.setBrush(hair)
        ellipse(cx - face_r - 6, cy - face_r - 4, (face_r + 6) * 2, (face_r + 10) * 2)

        # 脸部
        painter.setBrush(skin)
        ellipse(cx - face_r, cy - face_r, face_r * 2, face_r * 2)

        # 头发刘海：两个弧线覆盖额头
        painter.setBrush(hair)
        ellipse(cx - face_r - 2, cy - face_r - 6, face_r + 8, face_r * 0.85)
        ellipse(cx - 6, cy - face_r - 6, face_r + 8, face_r * 0.85)

        # 耳朵
        painter.setBrush(skin)
        ellipse(cx - face_r - 6, cy + 2, 10, 14)
        ellipse(cx + face_r - 4, cy + 2, 10, 14)

        # 眼睛：大圆眼 + 竖线瞳孔（现代极简）
        eye_r = max(6, face_r // 5)
        lx, ly = cx - face_r // 2 - 2, cy - face_r // 8
        rx, ry = cx + face_r // 2 + 2, cy - face_r // 8
        painter.setBrush(line_color)
        ellipse(lx - eye_r, ly - eye_r, eye_r * 2, eye_r * 2)
        ellipse(rx - eye_r, ry - eye_r, eye_r * 2, eye_r * 2)
        painter.setBrush(QColor(255, 255, 255))
        ellipse(lx - eye_r // 2, ly - eye_r // 2, eye_r // 2, eye_r // 2)
        ellipse(rx - eye_r // 2, ry - eye_r // 2, eye_r // 2, eye_r // 2)

        # 腮红
        painter.setBrush(blush)
        ellipse(cx - face_r + 10, cy + face_r // 4, face_r // 3, face_r // 5)
        ellipse(cx + face_r - 10 - face_r // 3, cy + face_r // 4, face_r // 3, face_r // 5)

        # 嘴巴：简洁微笑线
        line(cx - 6, cy + face_r // 3, cx, cy + face_r // 3 + 4, line_color, 2)
        line(cx, cy + face_r // 3 + 4, cx + 6, cy + face_r // 3, line_color, 2)

        # 身体：简单梯形/圆角矩形
        body_w = int(face_r * 1.4)
        body_h = int(size * 0.28)
        bx, by = cx - body_w // 2, cy + face_r - 4
        painter.setBrush(QColor(245, 245, 244))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bx, by, body_w, body_h, body_w // 2, body_w // 2)

        # 衣领装饰
        painter.setBrush(accent)
        ellipse(cx - 8, by + 8, 16, 10)
    finally:
        painter.end()
    pixmap.save(str(path), "PNG")


# ==================== 历史记录 ====================

def load_history():
    try:
        if HISTORY_PATH.exists():
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_history(records):
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def append_history_record(record):
    records = load_history()
    records.insert(0, record)
    records = records[:500]
    save_history(records)


def remove_history_record(index):
    records = load_history()
    if 0 <= index < len(records):
        records.pop(index)
        save_history(records)


def update_history_record(index, updates):
    records = load_history()
    if 0 <= index < len(records):
        records[index].update(updates)
        save_history(records)


# ==================== Bilibili API ====================

def _build_bili_session(settings):
    """构建带 Cookie 和代理的 B站请求 session。"""
    session = requests.Session()
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    # 注入 Cookie
    mode = settings.get("cookie_mode") or "none"
    if mode == "file":
        cookie_file = (settings.get("cookie_file") or "").strip()
        if cookie_file and Path(cookie_file).exists():
            try:
                cj = requests.cookies.MozillaCookieJar(cookie_file)
                cj.load(ignore_discard=True, ignore_expires=True)
                session.cookies = cj
            except Exception:
                pass
    elif mode in ("chrome", "edge", "firefox"):
        try:
            import browser_cookie3
            func = {"chrome": browser_cookie3.chrome,
                    "edge": browser_cookie3.edge,
                    "firefox": browser_cookie3.firefox}.get(mode)
            if func:
                cj = func(domain_name="bilibili.com")
                session.cookies = cj
        except Exception:
            pass
    session.headers.update(std_headers())
    return session


def bili_view(video_id, settings):
    """获取视频信息。video_id 可以是 BV 号或 av 号。"""
    session = _build_bili_session(settings)
    id_type, id_value = extract_video_id(video_id) if isinstance(video_id, str) else (None, None)
    if id_type == "aid":
        params = {"aid": id_value}
    elif id_type == "bvid":
        params = {"bvid": id_value}
    else:
        # 兼容直接传 BV 号的旧调用方式
        params = {"bvid": video_id}
    resp = session.get(
        BILIBILI_VIEW_API,
        params=params,
        headers=std_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or "B站视频信息接口返回失败。")
    return payload["data"]


def bili_playurl(video_id, cid, qn, settings, page=None, fnval=None):
    """获取播放地址。video_id 可以是 BV 号或 av 号。
    fnval: 格式标志位，None 表示自动（有 Cookie 用 4048，无 Cookie 用 0）。
    """
    session = _build_bili_session(settings)
    id_type, id_value = extract_video_id(video_id) if isinstance(video_id, str) else (None, None)
    # 自动选择 fnval：有 Cookie 时请求 DASH（高清），无 Cookie 时只请求 durl（低清但可下载）
    if fnval is None:
        has_cookie = (settings.get("cookie_mode") or "none") != "none"
        fnval = 4048 if has_cookie else 0
    if id_type == "aid":
        params = {"aid": id_value, "cid": cid, "qn": qn, "fnval": fnval, "fourk": 1}
    else:
        params = {"bvid": id_value or video_id, "cid": cid, "qn": qn, "fnval": fnval, "fourk": 1}
    if page:
        params["page"] = page
    resp = session.get(
        BILIBILI_PLAYURL_API,
        params=params,
        headers=std_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or "B站下载地址接口返回失败。")
    return payload["data"]


def save_cookies_to_netscape_file(cookies, path):
    """把 Cookie 字典保存为 Netscape 格式 cookies.txt。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for name, value in cookies.items():
            f.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")


def classify_bili_error(exc):
    """对 B站错误进行分类，返回 (类别, 说明) 或 None。"""
    msg = str(exc)
    if "HTTP Error 412" in msg or "Precondition Failed" in msg:
        return ("风控拦截", "B站 412 风控，请配置 Cookie 或稍后再试。")
    if "HTTP Error 403" in msg:
        return ("权限不足", "HTTP 403，可能需要登录或大会员。")
    if "HTTP Error 404" in msg:
        return ("视频不存在", "HTTP 404，视频可能已被删除。")
    for code, category, hint in BILI_ERROR_CATEGORIES:
        if str(code) in msg:
            return (category, hint)
    return None


def download_bili_danmaku(cid, output_path, settings):
    """下载 B站弹幕 XML。成功返回 True，失败返回 False。"""
    try:
        session = requests.Session()
        proxy = (settings.get("proxy") or "").strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        session.headers.update(std_headers())
        resp = session.get(
            BILIBILI_DM_LIST_API,
            params={"oid": cid},
            timeout=15,
        )
        resp.raise_for_status()
        try:
            content = resp.content.decode("utf-8", errors="ignore")
        except Exception:
            import zlib
            try:
                content = zlib.decompress(resp.content, -15).decode("utf-8", errors="ignore")
            except Exception:
                content = resp.content.decode("utf-8", errors="ignore")
        if not content or "<d " not in content:
            return False
        with open(output_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<i>\n')
            f.write(content)
            f.write('\n</i>\n')
        return True
    except Exception:
        return False


def fetch_bili_danmaku_for_url(url, output_base, settings):
    """为 B站 URL 下载弹幕，output_base 是不含扩展名的输出路径前缀。"""
    if not is_bilibili_url(url):
        return False
    id_type, id_value = extract_video_id(url)
    if not id_value:
        return False
    try:
        data = bili_view(url, settings)
        page_num = selected_page_number(url)
        pages = data.get("pages") or []
        cid = None
        for p in pages:
            if (p.get("page") or 1) == page_num:
                cid = p.get("cid")
                break
        if cid is None and pages:
            cid = pages[0].get("cid")
        if cid is None:
            return False
        output_path = f"{output_base}.danmaku.xml"
        return download_bili_danmaku(cid, output_path, settings)
    except Exception:
        return False


# ==================== 预览 Worker ====================

class PreviewWorker(QThread):
    info_ready = pyqtSignal(int, dict)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, url, settings, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.url = normalize_input(url)
        self.settings = settings

    def run(self):
        if not self.url:
            self.failed.emit(self.request_id, "没有可解析的链接。")
            return
        try:
            info = self.fetch_with_ytdlp()
            self.info_ready.emit(self.request_id, info)
        except Exception as exc:
            try:
                if is_bilibili_url(self.url) and (
                    "HTTP Error 412" in str(exc) or "Precondition Failed" in str(exc)
                ):
                    try:
                        info = self.fetch_bili_legacy_preview(str(exc))
                        self.info_ready.emit(self.request_id, info)
                        return
                    except Exception as fallback_exc:
                        self.failed.emit(self.request_id, format_bili_error(fallback_exc))
                        return
                self.failed.emit(self.request_id, format_bili_error(exc))
            except Exception as top_exc:
                write_crash_log(type(top_exc), top_exc, top_exc.__traceback__,
                                source="PreviewWorker")
                try:
                    self.failed.emit(self.request_id, format_bili_error(top_exc))
                except Exception:
                    pass

    def ytdlp_options(self):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "logger": QuietYtdlpLogger(),
            "http_headers": std_headers(),
        }
        return apply_cookie_and_proxy_options(opts, self.settings)

    def fetch_with_ytdlp(self):
        with yt_dlp.YoutubeDL(self.ytdlp_options()) as ydl:
            info = ydl.extract_info(self.url, download=False)
        return self.normalize_ytdlp_info(info)

    def normalize_ytdlp_info(self, info):
        formats = []
        for f in info.get("formats", []):
            formats.append({
                "format_id": f.get("format_id", ""),
                "ext": f.get("ext", ""),
                "vcodec": f.get("vcodec") or "",
                "acodec": f.get("acodec") or "",
                "width": f.get("width") or 0,
                "height": f.get("height") or 0,
                "fps": f.get("fps") or 0,
                "filesize": f.get("filesize") or f.get("filesize_approx") or 0,
                "tbr": f.get("tbr") or 0,
                "note": "",
            })
        pages = []
        entries = info.get("entries") or []
        if entries:
            for i, e in enumerate(entries, 1):
                pages.append({
                    "page": i,
                    "title": e.get("title") or f"P{i}",
                    "duration": e.get("duration") or 0,
                    "url": e.get("webpage_url") or self.url,
                })
        return {
            "title": info.get("title") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration") or 0,
            "thumbnail": info.get("thumbnail") or "",
            "view_count": info.get("view_count") or 0,
            "like_count": info.get("like_count") or 0,
            "upload_date": info.get("upload_date") or "",
            "pages": pages,
            "formats": formats,
            "source": "yt-dlp",
            "url": self.url,
            "note": "yt-dlp 解析成功" if formats else "未获取到格式列表",
        }

    def fetch_bili_legacy_preview(self, err_msg=""):
        id_type, id_value = extract_video_id(self.url)
        if not id_value:
            raise RuntimeError("无法从链接中识别 BV 号或 av 号。")
        data = bili_view(self.url, self.settings)
        pages = []
        for p in data.get("pages", []):
            pages.append({
                "page": p.get("page") or 1,
                "title": p.get("part") or f"P{p.get('page') or 1}",
                "duration": p.get("duration") or 0,
                "url": f"https://www.bilibili.com/video/{id_value}?p={p.get('page') or 1}" if id_type == "bvid" else f"https://www.bilibili.com/video/av{id_value}?p={p.get('page') or 1}",
            })
        if not pages:
            raise RuntimeError("没有找到可预览的分 P。")
        return {
            "title": data.get("title") or "",
            "uploader": data.get("owner", {}).get("name") or "",
            "duration": data.get("duration") or 0,
            "thumbnail": data.get("pic") or "",
            "view_count": data.get("stat", {}).get("view") or 0,
            "like_count": data.get("stat", {}).get("like") or 0,
            "upload_date": "",
            "pages": pages,
            "formats": [],
            "source": "B站公开接口（兜底）",
            "url": self.url,
            "note": f"yt-dlp 失败({err_msg[:40]})，已用兜底接口。无 Cookie 时通常仅 720P/360P。",
        }


# ==================== 下载 Worker ====================

class DownloadWorker(QThread):
    item_started = pyqtSignal(int, str)
    item_progress = pyqtSignal(int, float, str)
    item_finished = pyqtSignal(int, str, str)
    item_failed = pyqtSignal(int, str)
    item_history = pyqtSignal(dict)
    log = pyqtSignal(str)
    all_done = pyqtSignal(bool)
    paused_changed = pyqtSignal(bool)

    def __init__(self, inputs, settings, parent=None):
        super().__init__(parent)
        self.inputs = inputs
        self.settings = settings
        self.cancelled = False
        self.paused = False
        self.skip_indices = set()
        self.current_titles = {}
        self.current_filename = ""

    def cancel(self):
        self.cancelled = True
        self.paused = False

    def pause(self):
        if not self.cancelled and not self.paused:
            self.paused = True
            self.paused_changed.emit(True)

    def resume(self):
        if self.paused:
            self.paused = False
            self.paused_changed.emit(False)

    def skip_index(self, index):
        self.skip_indices.add(index)

    def wait_while_paused(self, index):
        while self.paused and not self.cancelled:
            time.sleep(0.2)
        return not self.cancelled and index not in self.skip_indices

    def run(self):
        ok = True
        try:
            Path(self.settings["download_dir"]).mkdir(parents=True, exist_ok=True)
            concurrent = max(1, int(self.settings.get("concurrent_downloads", 1)))
            if concurrent == 1:
                for index, raw in enumerate(self.inputs):
                    status = self._process_one(index, raw)
                    if status == "cancelled":
                        ok = False
                        break
                    if status == "failed":
                        ok = False
            else:
                ok = self._run_concurrent(concurrent)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker")
            ok = False
            try:
                self.log.emit(f"下载线程异常: {format_error(exc)}")
            except Exception:
                pass

        try:
            if self.cancelled:
                self.cleanup_temp_files()
            self.all_done.emit(ok and not self.cancelled)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.all_done")

    def _process_one(self, index, raw):
        """处理单个下载任务。返回: 'ok' | 'failed' | 'cancelled' | 'skipped'。"""
        if self.cancelled:
            return "cancelled"
        if index in self.skip_indices:
            self.item_failed.emit(index, "已从队列删除")
            return "skipped"
        if not self.wait_while_paused(index):
            if self.cancelled:
                return "cancelled"
            self.item_failed.emit(index, "已从队列删除")
            return "skipped"

        url = normalize_input(raw)
        if not url:
            return "skipped"

        self.current_titles[index] = url
        self.item_started.emit(index, url)
        self.log.emit(f"开始处理: {url}")
        started_at = int(time.time())
        try:
            if self.should_use_bili_legacy(url):
                outputs = self.download_bili_legacy(index, url)
                output_detail = self.output_detail(outputs)
            else:
                output = self.download_with_ytdlp(index, url)
                output_detail = self.output_detail([output])
            # 下载弹幕
            if self.settings.get("download_danmaku") and is_bilibili_url(url):
                self.item_progress.emit(index, 100, "正在下载弹幕...")
                output_base = self._output_base_for_danmaku(output_detail)
                if output_base:
                    if fetch_bili_danmaku_for_url(url, output_base, self.settings):
                        self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    else:
                        self.log.emit("弹幕下载失败或无弹幕")
            self.item_finished.emit(index, output_detail, "完成")
            self.log.emit(f"完成: {output_detail}")
            self.emit_history(index, url, output_detail, "completed", "", started_at)
            return "ok"
        except Exception as exc:
            if self.cancelled:
                self.item_failed.emit(index, "已取消")
                self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                return "cancelled"
            if self.should_try_bili_fallback(url, exc):
                self.log.emit("yt-dlp 被 B站 412 拦截，尝试公开视频兜底接口...")
                try:
                    outputs = self.download_bili_legacy(index, url)
                    output_text = self.output_detail(outputs)
                    if self.settings.get("download_danmaku"):
                        self.item_progress.emit(index, 100, "正在下载弹幕...")
                        output_base = self._output_base_for_danmaku(output_text)
                        if output_base and fetch_bili_danmaku_for_url(url, output_base, self.settings):
                            self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    self.item_finished.emit(index, output_text, "完成（公开视频兜底）")
                    self.log.emit(f"兜底完成: {output_text}")
                    self.emit_history(index, url, output_text, "completed", "", started_at)
                    return "ok"
                except Exception as fallback_exc:
                    if self.cancelled:
                        self.item_failed.emit(index, "已取消")
                        self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                        return "cancelled"
                    err_text = format_bili_error(fallback_exc)
                    self.item_failed.emit(index, err_text)
                    self.log.emit(f"兜底失败: {err_text}")
                    self.emit_history(index, url, "", "failed", err_text, started_at)
                    return "failed"
            err_text = format_bili_error(exc)
            self.item_failed.emit(index, err_text)
            self.log.emit(f"失败: {err_text}")
            self.emit_history(index, url, "", "failed", err_text, started_at)
            return "failed"

    def _run_concurrent(self, max_workers):
        """并发下载多个任务。返回 True 表示全部成功。"""
        ok = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_one, index, raw): index
                for index, raw in enumerate(self.inputs)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    status = future.result()
                    if status in ("failed", "cancelled"):
                        ok = False
                        if status == "cancelled":
                            for f in futures:
                                f.cancel()
                except Exception as exc:
                    write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.concurrent")
                    ok = False
                    try:
                        self.log.emit(f"并发下载异常: {format_error(exc)}")
                    except Exception:
                        pass
        return ok

    def emit_history(self, index, url, output_detail, status, error, started_at):
        title = self.current_titles.get(index) or url
        record = {
            "title": title,
            "url": url,
            "output_detail": output_detail,
            "output_path": extract_output_path(output_detail),
            "status": status,
            "error": error,
            "format_id": (self.settings.get("custom_format") or "").strip(),
            "quality": self.settings.get("quality", ""),
            "created_at": started_at,
            "finished_at": int(time.time()),
        }
        output_path = record["output_path"]
        if output_path and Path(output_path).exists():
            info = media_info_for_file(output_path)
            record.update({
                "file_size": info.get("file_size", 0),
                "duration": info.get("duration", ""),
                "resolution": f"{info.get('width', '')}x{info.get('height', '')}" if info.get("width") else "",
                "fps": info.get("fps", ""),
                "video_codec": info.get("video_codec", ""),
                "audio_codec": info.get("audio_codec", ""),
            })
        self.item_history.emit(record)

    def output_detail(self, outputs):
        return "\n".join(str(o) for o in outputs if o)

    def _output_base_for_danmaku(self, output_detail):
        """从输出详情中提取弹幕文件的基础路径（去掉扩展名）。"""
        path = extract_output_path(output_detail)
        if not path:
            return ""
        p = Path(path)
        return str(p.parent / p.stem)

    def should_use_bili_legacy(self, url):
        """B站链接优先走公开视频兜底接口（能绕过 412 风控）。
        yt-dlp 直接请求 B站 API 经常被 412 拦截，且 quiet 模式下错误被吞掉，
        导致 download() 返回成功但实际没下载任何内容。"""
        return is_bilibili_url(url)

    def should_try_bili_fallback(self, url, exc):
        if not is_bilibili_url(url):
            return False
        msg = str(exc)
        return "HTTP Error 412" in msg or "Precondition Failed" in msg

    def build_ytdlp_options(self, index):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": int(self.settings.get("fragment_threads", 4)),
            "outtmpl": str(Path(self.settings["download_dir"]) / self.settings["filename_template"]),
            "logger": QuietYtdlpLogger(),
            "http_headers": std_headers(),
            "progress_hooks": [lambda d: self.on_ytdlp_progress(index, d)],
            "postprocessor_hooks": [self.on_ytdlp_postprocess],
        }
        custom_format = (self.settings.get("custom_format") or "").strip()
        if custom_format:
            opts["format"] = custom_format
        else:
            quality = self.settings.get("quality", "best")
            audio_q = self.settings.get("audio_quality", "auto")
            if quality == "audio":
                if audio_q == "high":
                    opts["format"] = "bestaudio[abr>=192]/bestaudio/best"
                elif audio_q == "medium":
                    opts["format"] = "bestaudio[abr>=128]/bestaudio/best"
                else:
                    opts["format"] = "bestaudio/best"
            elif quality == "best":
                opts["format"] = "bestvideo+bestaudio/best"
            else:
                qn = QUALITY_QN.get(quality, 80)
                opts["format"] = f"bestvideo[height<={qn}]+bestaudio/best[height<={qn}]/best"
            codec = self.settings.get("codec_preference", "auto")
            if codec == "h264":
                opts["format_sort"] = ["vcodec:h264"]
            elif codec == "hevc":
                opts["format_sort"] = ["vcodec:hevc"]
            elif codec == "av1":
                opts["format_sort"] = ["vcodec:av1"]
            # 音频质量偏好（非仅音频模式也应用）
            if quality != "audio" and audio_q != "auto":
                if audio_q == "high":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:192"]
                elif audio_q == "medium":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:128"]
        if self.settings.get("download_thumbnail"):
            opts["writethumbnail"] = True
        if self.settings.get("download_subtitle"):
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = ["zh-Hans", "zh", "en"]
        if FFMPEG_EXE.exists():
            opts["ffmpeg_location"] = str(FFMPEG_EXE.parent)
        return apply_cookie_and_proxy_options(opts, self.settings)

    def on_ytdlp_progress(self, index, data):
        if self.cancelled:
            raise RuntimeError("用户取消下载")
        status = data.get("status")
        filename = data.get("filename") or data.get("tmpfilename") or ""
        if filename:
            self.current_filename = filename
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = downloaded / total * 100 if total else 0
            speed = data.get("speed") or 0
            eta = data.get("eta")
            detail = f"{percent:.1f}%"
            if speed:
                detail += f"  {speed / 1024 / 1024:.2f} MB/s"
            if eta is not None:
                detail += f"  ETA {eta}s"
            self.item_progress.emit(index, percent, detail)
        elif status == "finished":
            self.item_progress.emit(index, 100, "下载完成，正在合并/整理")
        elif status == "error":
            self.item_progress.emit(index, 0, "下载错误")

    def on_ytdlp_postprocess(self, d):
        """后处理钩子：合并/转码完成后更新最终文件名。
        DASH 流下载时 progress_hook 记录的是临时分片文件，合并后会被删除，
        这里用 postprocessor 的 filepath 覆盖为最终输出文件，确保历史记录能取到真实路径。"""
        try:
            filepath = d.get("filepath") or ""
            if filepath:
                self.current_filename = filepath
        except Exception:
            pass

    def download_with_ytdlp(self, index, url):
        self.current_filename = ""
        with yt_dlp.YoutubeDL(self.build_ytdlp_options(index)) as ydl:
            result = ydl.download([url])
        if result:
            raise RuntimeError(f"yt-dlp 返回错误码: {result}")
        final = self.current_filename or ""
        # 验证：如果最终文件不存在（可能是 yt-dlp 静默失败，如 412 风控），抛错而非返回目录
        if not final or not Path(final).is_file():
            raise RuntimeError(
                "yt-dlp 未生成有效文件（可能被 B站 412 风控拦截或格式无效）。\n"
                "建议：清除选定的格式（留空）后重试，或配置 Cookie 后重试。"
            )
        return final

    def request_session(self):
        """构建带 Cookie 和代理的下载 session。"""
        session = _build_bili_session(self.settings)
        return session

    def download_bili_legacy(self, index, url):
        id_type, id_value = extract_video_id(url)
        if not id_value:
            raise RuntimeError("无法从链接中识别 BV 号或 av 号。")
        video_id = url  # bili_view/bili_playurl 内部会解析
        page_num = selected_page_number(url)
        data = bili_view(video_id, self.settings)
        pages = data.get("pages") or []
        if not pages:
            raise RuntimeError("没有找到可下载的分 P。")
        cid = None
        for p in pages:
            if (p.get("page") or 1) == page_num:
                cid = p.get("cid")
                break
        if cid is None:
            cid = pages[0].get("cid")
            page_num = pages[0].get("page") or 1
        quality = self.settings.get("quality", "best")
        if quality == "audio":
            raise RuntimeError("B站公开视频兜底接口不支持仅音频。请配置 Cookie 后重试。")
        has_cookie = (self.settings.get("cookie_mode") or "none") != "none"
        # 如果用户在预览表选了具体格式（如 30080 / 30080+30280），从中解析出视频清晰度 qn
        # B站 DASH 流 format_id：视频 300xx，音频 302xx；qn = format_id - 30000
        custom_format = (self.settings.get("custom_format") or "").strip()
        qn_from_format = None
        if custom_format:
            for part in custom_format.split("+"):
                part = part.strip()
                if part.isdigit() and part.startswith("300"):
                    qn_from_format = int(part) - 30000
                    break
        if qn_from_format:
            qn = qn_from_format
            self.log.emit(f"按选定格式解析清晰度 qn={qn}")
        elif not has_cookie:
            qn = 16  # 360P
            self.log.emit("未配置 Cookie，兜底接口尝试 360P 低清晰度...")
        else:
            qn = QUALITY_QN.get(quality, 80) if quality != "best" else 80
        play_data = bili_playurl(video_id, cid, qn, self.settings, page=page_num)
        durl = play_data.get("durl") or []
        if not durl:
            # 如果有 Cookie 且返回了 dash，尝试用 fnval=0 重新请求 durl
            dash = play_data.get("dash")
            if dash:
                self.log.emit("返回了 DASH 格式，尝试请求 durl 直链...")
                play_data = bili_playurl(video_id, cid, qn, self.settings, page=page_num, fnval=0)
                durl = play_data.get("durl") or []
            if not durl:
                raise RuntimeError(
                    "兜底接口没有返回可下载直链。可能原因：\n"
                    "1. 视频需要登录/大会员 → 请配置 Cookie\n"
                    "2. 视频被删除/审核中\n"
                    "3. B站风控 → 请稍后再试或配置 Cookie\n"
                    "建议：设置页 → 扫码登录配置 Cookie 后重试。"
                )
        session = self.request_session()
        download_dir = Path(self.settings["download_dir"])
        title = sanitize_filename(data.get("title") or id_value)
        if len(pages) > 1:
            page_part = pages[0]
            for p in pages:
                if (p.get("page") or 1) == page_num:
                    page_part = p
                    break
            part_title = sanitize_filename(page_part.get("part") or f"P{page_num}")
            filename = f"{title}_P{page_num}_{part_title}.mp4"
        else:
            filename = f"{title}.mp4"
        output_path = unique_path(download_dir / filename)
        outputs = []
        total_bytes = 0
        for i, d in enumerate(durl):
            video_url = d.get("url")
            if not video_url:
                continue
            if self.cancelled:
                raise RuntimeError("用户取消下载")
            self.item_progress.emit(index, 0, f"下载分段 {i+1}/{len(durl)}")
            resp = session.get(video_url, headers={"Referer": "https://www.bilibili.com/"}, stream=True, timeout=30)
            resp.raise_for_status()
            size = int(d.get("size") or 0)
            downloaded = 0
            part_path = output_path.with_suffix(f".part{i}") if len(durl) > 1 else output_path.with_suffix(".part")
            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if self.cancelled:
                        raise RuntimeError("用户取消下载")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        total_bytes += len(chunk)
                        if size:
                            pct = downloaded / size * 100
                            self.item_progress.emit(index, pct, f"分段 {i+1}/{len(durl)}  {pct:.1f}%")
            if len(durl) > 1:
                outputs.append(str(part_path))
            else:
                part_path.replace(output_path)
                outputs.append(str(output_path))
        if len(durl) > 1 and FFMPEG_EXE.exists():
            concat_path = output_path.with_suffix(".concat.txt")
            with open(concat_path, "w", encoding="utf-8") as f:
                for o in outputs:
                    f.write(f"file '{o}'\n")
            try:
                subprocess.run(
                    [str(FFMPEG_EXE), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
                     "-c", "copy", str(output_path)],
                    check=True,
                    timeout=300,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for o in outputs:
                    try:
                        Path(o).unlink()
                    except Exception:
                        pass
                try:
                    concat_path.unlink()
                except Exception:
                    pass
                outputs = [str(output_path)]
            except Exception:
                pass
        self.item_progress.emit(index, 100, "完成")
        return outputs

    def cleanup_temp_files(self):
        """清理临时文件。"""
        try:
            download_dir = Path(self.settings["download_dir"])
            if not download_dir.exists():
                return
            for pattern in ["*.part", "*.part0", "*.part1", "*.ytdl", "*.temp", "*.concat.txt"]:
                for f in download_dir.glob(pattern):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        except Exception:
            pass


# ==================== Cookie 检测 Worker ====================

class CookieCheckWorker(QThread):
    result_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self):
        try:
            cookies = self.load_cookies()
            if cookies is None:
                self.result_ready.emit({"logged_in": False, "reason": "无法读取 Cookie"})
                return
            session = requests.Session()
            session.cookies.update(cookies)
            proxy = (self.settings.get("proxy") or "").strip()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
            response = session.get(
                BILIBILI_NAV_API,
                headers=std_headers("https://www.bilibili.com/"),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            is_logged = bool(data.get("isLogin"))
            result = {
                "logged_in": is_logged,
                "username": data.get("uname") or "",
                "mid": data.get("mid") or "",
                "vip_type": data.get("vipType") or 0,
                "vip_status": data.get("vipStatus") or 0,
                "sessdata_found": any(c.name == "SESSDATA" for c in session.cookies),
            }
            self.result_ready.emit(result)
        except Exception as exc:
            try:
                self.failed.emit(format_error(exc))
            except Exception as top_exc:
                write_crash_log(type(top_exc), top_exc, top_exc.__traceback__,
                                source="CookieCheckWorker")

    def load_cookies(self):
        mode = self.settings.get("cookie_mode") or "none"
        if mode == "none":
            return {}
        if mode == "file":
            cookie_file = (self.settings.get("cookie_file") or "").strip()
            if not cookie_file:
                return None
            if not Path(cookie_file).exists():
                return None
            try:
                cj = requests.cookies.MozillaCookieJar(cookie_file)
                cj.load(ignore_discard=True, ignore_expires=True)
                return {c.name: c.value for c in cj}
            except Exception:
                return None
        try:
            import browser_cookie3
            func = {"chrome": browser_cookie3.chrome,
                    "edge": browser_cookie3.edge,
                    "firefox": browser_cookie3.firefox}.get(mode)
            if not func:
                return None
            cj = func(domain_name="bilibili.com")
            return {c.name: c.value for c in cj if "bilibili.com" in c.domain}
        except Exception:
            return None


# ==================== 扫码登录 Worker ====================

class QrLoginWorker(QThread):
    """B站扫码登录：生成二维码并轮询登录状态。"""
    qrcode_ready = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    login_success = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.cancelled = False
        self.qrcode_key = ""
        self.qrcode_url = ""

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            if not self.generate_qrcode():
                return
            self.poll_loop()
        except Exception as exc:
            try:
                self.failed.emit(format_error(exc))
            except Exception as top_exc:
                write_crash_log(type(top_exc), top_exc, top_exc.__traceback__,
                                source="QrLoginWorker")

    def build_session(self):
        session = requests.Session()
        proxy = (self.settings.get("proxy") or "").strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        session.headers.update(std_headers("https://www.bilibili.com/"))
        return session

    def generate_qrcode(self):
        session = self.build_session()
        resp = session.get(BILIBILI_QRCODE_GENERATE_API, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            self.failed.emit(payload.get("message") or "二维码生成失败")
            return False
        data = payload.get("data") or {}
        self.qrcode_key = data.get("qrcode_key") or ""
        self.qrcode_url = data.get("url") or ""
        if not self.qrcode_url:
            self.failed.emit("二维码生成失败：未返回 url")
            return False
        self.qrcode_ready.emit(self.qrcode_url)
        self.status_changed.emit("请使用 B站手机客户端扫码")
        return True

    def poll_loop(self):
        session = self.build_session()
        start = time.time()
        timeout = 180
        while not self.cancelled:
            if time.time() - start > timeout:
                self.status_changed.emit("二维码已过期")
                self.failed.emit("二维码已过期，请重新生成")
                return
            try:
                resp = session.get(
                    BILIBILI_QRCODE_POLL_API,
                    params={"qrcode_key": self.qrcode_key},
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json()
                code = payload.get("data", {}).get("code", 0)
                if code == 0:
                    cookies = {}
                    for c in session.cookies:
                        cookies[c.name] = c.value
                    refresh_url = payload.get("data", {}).get("url", "")
                    if refresh_url:
                        from urllib.parse import parse_qs as _pqs, urlparse as _url
                        params = _pqs(_url(refresh_url).query)
                        for k, v in params.items():
                            if k.startswith("DedeUserID") or k in ("SESSDATA", "bili_jct", "DedeUserID__ckMd5"):
                                cookies[k] = v[0]
                    self.status_changed.emit("登录成功！")
                    self.login_success.emit(cookies)
                    return
                elif code == 86101:
                    self.status_changed.emit("等待扫码...")
                elif code == 86090:
                    self.status_changed.emit("已扫码，请在手机上确认")
                elif code == 86038:
                    self.status_changed.emit("二维码已过期")
                    self.failed.emit("二维码已过期，请重新生成")
                    return
                else:
                    msg = payload.get("data", {}).get("message") or f"未知状态: {code}"
                    self.status_changed.emit(msg)
            except Exception as exc:
                self.status_changed.emit(f"查询异常: {format_error(exc)}")
            time.sleep(2)


# ==================== 扫码登录对话框 ====================

class QrLoginDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.worker = None
        self.cookie_file_path = ""
        self.setWindowTitle("扫码登录 B站")
        self.resize(360, 460)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("扫码登录")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #FB7299;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("用 B站手机客户端扫描下方二维码")
        hint.setStyleSheet("color: #6b7280;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self.qr_label = QLabel("正在生成二维码...")
        self.qr_label.setFixedSize(240, 240)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet("background: #ffffff; border: 2px dashed #FB7299; border-radius: 12px; color: #6b7280;")
        layout.addWidget(self.qr_label, alignment=Qt.AlignCenter)

        self.status_label = QLabel("准备中...")
        self.status_label.setStyleSheet("font-size: 14px; color: #1a1a2e;")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.refresh_btn = QPushButton("重新生成二维码")
        self.refresh_btn.clicked.connect(self.start)
        layout.addWidget(self.refresh_btn)

    def start(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        self.qr_label.setText("正在生成二维码...")
        self.status_label.setText("准备中...")
        self.worker = QrLoginWorker(self.settings, self)
        self.worker.qrcode_ready.connect(self.render_qrcode)
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.login_success.connect(self.on_login_success)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def render_qrcode(self, content):
        try:
            import qrcode
            from PyQt5.QtGui import QImage, QPainter, QColor
            qr = qrcode.QRCode(border=2)
            qr.add_data(content)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            size = len(matrix)
            cell = 6
            img = QImage(size * cell, size * cell, QImage.Format_RGB32)
            img.fill(QColor("#ffffff"))
            painter = QPainter(img)
            painter.setBrush(QColor("#1a1a2e"))
            painter.setPen(Qt.NoPen)
            for y, row in enumerate(matrix):
                for x, v in enumerate(row):
                    if v:
                        painter.drawRect(x * cell, y * cell, cell, cell)
            painter.end()
            pixmap = QPixmap.fromImage(img).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.qr_label.setPixmap(pixmap)
        except Exception as exc:
            self.qr_label.setText(f"二维码渲染失败:\n{format_error(exc)}")

    def on_login_success(self, cookies):
        cookie_path = BASE_DIR / "cookies.txt"
        try:
            save_cookies_to_netscape_file(cookies, cookie_path)
            self.cookie_file_path = str(cookie_path)
            self.status_label.setText("登录成功！Cookie 已保存。")
            QMessageBox.information(self, "扫码登录", f"登录成功！\nCookie 已保存到:\n{cookie_path}")
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"Cookie 保存失败: {format_error(exc)}")

    def on_failed(self, msg):
        self.status_label.setText(f"失败: {msg}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()


# ==================== 柔和几何粒子覆盖层 ====================

class SakuraOverlay(QWidget):
    """透明覆盖层，绘制柔和浮动的几何粒子（圆点/小圆环），现代极简。"""

    COLORS = [
        QColor(251, 146, 60),
        QColor(244, 114, 182),
        QColor(167, 139, 250),
        QColor(45, 212, 191),
        QColor(250, 204, 21),
    ]

    def __init__(self, parent=None, count=30):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.particles = []
        for _ in range(count):
            self.particles.append(self._reset_particle())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_particles)
        self.timer.start(50)

    def _reset_particle(self, y=None):
        w = self.width() or 800
        return {
            "x": random.randint(0, max(w, 100)),
            "y": y if y is not None else random.randint(-300, 0),
            "speed": random.uniform(0.6, 2.2),
            "sway": random.uniform(0.2, 0.8),
            "phase": random.uniform(0, 6.28),
            "size": random.randint(4, 10),
            "ring": random.random() > 0.6,
            "color": random.choice(self.COLORS),
            "alpha": random.randint(40, 100),
        }

    def resizeEvent(self, event):
        for p in self.particles:
            if p["x"] > self.width():
                p["x"] = self.width() - 10
        super().resizeEvent(event)

    def _update_particles(self):
        for i, p in enumerate(self.particles):
            p["y"] += p["speed"]
            p["x"] += math.sin(p["phase"]) * p["sway"]
            p["phase"] += 0.03
            if p["y"] > self.height() + 30:
                self.particles[i] = self._reset_particle(y=-30)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for p in self.particles:
            c = QColor(p["color"])
            c.setAlpha(p["alpha"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            r = p["size"] / 2
            painter.drawEllipse(int(p["x"] - r), int(p["y"] - r), int(p["size"]), int(p["size"]))
            if p["ring"]:
                c2 = QColor(c)
                c2.setAlpha(p["alpha"] // 2)
                pen = QPen(c2)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(int(p["x"] - r - 3), int(p["y"] - r - 3), int(p["size"] + 6), int(p["size"] + 6))
        painter.end()


# ==================== 柔和发光卡片 ====================

class NeonGlowCard(QFrame):
    """柔和呼吸发光边框的卡片（玻璃拟态风格）。"""

    def __init__(self, title=None, glow_color="#FB7299"):
        super().__init__()
        self.setObjectName("card")
        self.glow_color = QColor(glow_color)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 18)
        self.layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("card-title")
            self.layout.addWidget(title_label)
        self._glow_alpha = 60
        self._glow_enabled = True
        self.anim = QPropertyAnimation(self, b"glow_alpha")
        self.anim.setDuration(2500)
        self.anim.setStartValue(40)
        self.anim.setEndValue(90)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.finished.connect(self._reverse_glow)
        self.anim.start()

    def _reverse_glow(self):
        self.anim.setStartValue(self.anim.endValue())
        self.anim.setEndValue(40 if self.anim.endValue() > 65 else 90)
        self.anim.start()

    def get_glow_alpha(self):
        return self._glow_alpha

    def set_glow_alpha(self, value):
        self._glow_alpha = value
        self.update()

    glow_alpha = property(get_glow_alpha, set_glow_alpha)

    def set_glow_enabled(self, enabled):
        self._glow_enabled = enabled
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._glow_enabled:
            rect = self.rect().adjusted(2, 2, -2, -2)
            # 柔和外发光层
            for i in range(4, 0, -1):
                alpha = int(self._glow_alpha * (i / 4.0) * 0.35)
                pen = QPen(QColor(self.glow_color.red(), self.glow_color.green(), self.glow_color.blue(), alpha))
                pen.setWidth(i * 2)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-i, -i, i, i), 22, 22)
        painter.end()
        super().paintEvent(event)


# ==================== 音效播放器 ====================

class SoundPlayer:
    """管理音效 WAV 文件并在交互时播放。"""

    SOUNDS = {
        "start": (880, 150),
        "success": (523, 120),
        "success2": (659, 120),
        "fail": (220, 250),
        "click": (1200, 60),
        "complete": (523, 150),
    }

    def __init__(self, resource_dir):
        self.resource_dir = Path(resource_dir)
        self.resource_dir.mkdir(parents=True, exist_ok=True)
        self._cache = {}
        self.enabled = True

    def _ensure_wav(self, name):
        path = self.resource_dir / f"{name}.wav"
        if not path.exists():
            freq, duration = self.SOUNDS.get(name, (440, 150))
            self._generate_beep(path, freq, duration)
        return path

    def _generate_beep(self, path, freq, duration_ms):
        import math, wave, struct
        sample_rate = 22050
        samples = int(sample_rate * duration_ms / 1000.0)
        with wave.open(str(path), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            for i in range(samples):
                t = i / sample_rate
                envelope = max(0.0, 1.0 - i / samples)
                val = int(32767 * 0.3 * envelope * math.sin(2 * math.pi * freq * t))
                w.writeframes(struct.pack("<h", val))

    def play(self, name):
        if not self.enabled:
            return
        try:
            path = self._ensure_wav(name)
            if name not in self._cache:
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setVolume(0.4)
                self._cache[name] = effect
            self._cache[name].play()
        except Exception:
            pass


# ==================== 可拖拽输入框 ====================

class DroppablePlainTextEdit(QPlainTextEdit):
    """支持拖拽链接/BV 号到输入框的纯文本编辑器。"""

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasText() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasText() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        texts = []
        if mime.hasUrls():
            for url in mime.urls():
                texts.append(url.toString())
        elif mime.hasText():
            texts.append(mime.text())
        else:
            event.ignore()
            return

        dropped = " ".join(texts)
        candidates = split_inputs(dropped)
        normalized = []
        for c in candidates:
            c = c.strip()
            if not c:
                continue
            # 浏览器拖拽有时会带标题和 URL 一起，取看起来像链接或 BV/av 号的部分
            if c.startswith("http://") or c.startswith("https://"):
                normalized.append(c)
            elif re.fullmatch(r"BV[0-9A-Za-z]{10,}", c) or re.fullmatch(r"[aA][vV]\d+", c):
                normalized.append(normalize_input(c))
            else:
                # 尝试从一行文本中提取 URL
                m = re.search(r"(https?://\S+)", c)
                if m:
                    normalized.append(m.group(1))

        if not normalized:
            event.ignore()
            return

        existing = self.toPlainText().strip()
        new_links = "\n".join(normalized)
        if existing:
            self.setPlainText(existing + "\n" + new_links)
        else:
            self.setPlainText(new_links)
        event.acceptProposedAction()


# ==================== 主窗口 ====================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings, load_err = load_settings()
        if load_err:
            print(f"设置加载失败，使用默认设置: {load_err}")
        self.worker = None
        self.preview_worker = None
        self.cookie_check_worker = None
        self.preview_request_id = 0
        self._force_quit = False
        self.preview_pending = False
        self.preview_formats = []
        self.history_records = load_history()
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.start_preview)
        self.base_window_title = "Bilibili 视频下载器"
        self.setWindowTitle(self.base_window_title)
        self.resize(1280, 860)
        self.setMinimumSize(900, 600)
        self._drag_pos = None
        self._is_maximized = False
        self.setWindowFlags(Qt.FramelessWindowHint)
        app_icon = self._app_icon_path()
        if app_icon:
            self.setWindowIcon(QIcon(str(app_icon)))
        self.sound_player = SoundPlayer(RESOURCE_DIR / "sounds")
        self.sakura_overlay = None
        self._init_mascot_image()
        self.init_ui()
        self.apply_settings_to_ui()
        self.refresh_history()
        self.init_tray_icon()
        self._setup_shortcuts()
        self.statusBar().showMessage("就绪")
        self.sound_player.play("click")

    def _app_icon_path(self):
        """返回应用图标路径，优先使用 icon.png，其次 icon.ico。"""
        for name in ("icon.png", "icon.ico"):
            path = RESOURCE_DIR / name
            if path.exists():
                return path
        return None

    def _init_mascot_image(self):
        """确认侧边栏看板娘图片：优先使用 icon.png，缺失时生成 mascot.png。"""
        icon_path = self._app_icon_path()
        if icon_path and icon_path.suffix.lower() == ".png":
            self.mascot_image_path = icon_path
        else:
            self.mascot_image_path = RESOURCE_DIR / "mascot.png"
            if not self.mascot_image_path.exists():
                try:
                    generate_mascot_image(self.mascot_image_path, size=200)
                except Exception as exc:
                    print("生成看板娘图片失败:", exc)

    def init_tray_icon(self):
        self.tray_icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app_icon = self._app_icon_path()
        icon = QIcon(str(app_icon)) if app_icon else QIcon()
        if icon.isNull():
            return
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Bilibili 视频下载器")
        # 右键菜单
        menu = QMenu(self)
        act_show = menu.addAction("显示主窗口")
        act_show.triggered.connect(self._tray_show_window)
        act_pause = menu.addAction("暂停 / 继续")
        act_pause.triggered.connect(self.toggle_pause_queue)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self._tray_quit)
        self.tray_icon.setContextMenu(menu)
        # 单击/双击还原
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._tray_show_window()

    def _tray_show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._force_quit = True
        self.close()

    def _setup_shortcuts(self):
        """注册全局键盘快捷键。"""
        # Ctrl+Enter: 开始下载
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start_downloads)
        # Ctrl+D: 清空输入
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.clear_input)
        # Ctrl+Shift+V: 粘贴并解析
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, activated=lambda: self.start_preview(force=True))
        # Space: 暂停/继续
        QShortcut(QKeySequence("Space"), self, activated=self.toggle_pause_queue)
        # Esc: 取消下载
        QShortcut(QKeySequence("Escape"), self, activated=self.cancel_downloads)
        # F5: 刷新历史
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_history)
        # Ctrl+1/2/3: 切换页面
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.switch_page(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.switch_page(1))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self.switch_page(2))
        # Ctrl+W: 最小化到托盘
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._minimize_to_tray)

    def _minimize_to_tray(self):
        if self.tray_icon:
            self.hide()
            self.tray_icon.showMessage(
                "Bilibili 视频下载器",
                "已最小化到托盘，单击图标恢复",
                QSystemTrayIcon.Information, 2000,
            )
        else:
            self.showMinimized()

    def create_card(self, title, glow=True, glow_color="#FB7299"):
        """创建一个带标题的圆角卡片容器，glow=True 时带霓虹呼吸边框。"""
        if glow and self.settings.get("fx_neon", True):
            card = NeonGlowCard(title=title, glow_color=glow_color)
            return card, card.layout
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("card-title")
            layout.addWidget(title_label)
        return card, layout

    def switch_page(self, index):
        """切换右侧页面，同步侧边栏按钮状态。"""
        self.content_stack.setCurrentIndex(index)
        self.nav_download_btn.setChecked(index == 0)
        self.nav_history_btn.setChecked(index == 1)
        self.nav_settings_btn.setChecked(index == 2)

    def init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ===== 自定义标题栏 =====
        title_bar = self._build_title_bar()
        root_layout.addWidget(title_bar)

        # ===== 主内容区 =====
        self.body_widget = QWidget()
        body_layout = QHBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        body_layout.addWidget(sidebar)

        # 右侧内容区（含缩放抓手）
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("contentStack")
        content_layout.addWidget(self.content_stack, 1)
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 0, 0)
        grip_layout.addStretch()
        grip_layout.addWidget(QSizeGrip(content_container))
        content_layout.addLayout(grip_layout)
        body_layout.addWidget(content_container, 1)

        root_layout.addWidget(self.body_widget, 1)

        # 樱花飘落层
        self.sakura_overlay = SakuraOverlay(self.body_widget, count=40)
        self.sakura_overlay.setGeometry(self.body_widget.rect())
        self.sakura_overlay.show()

        self._build_download_page()
        self._build_history_page()
        self._build_settings_page()
        self._apply_theme()

    def _build_title_bar(self):
        """构建现代极简标题栏：柔和背景、文字图标、圆角控制按钮。"""
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setObjectName("titleIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignCenter)
        app_icon = self._app_icon_path()
        if app_icon:
            icon_label.setPixmap(
                QPixmap(str(app_icon)).scaled(
                    22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

        self.title_label = QLabel(self.base_window_title)
        self.title_label.setObjectName("titleLabel")
        layout.addWidget(icon_label)
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.min_btn = QPushButton("−")
        self.min_btn.setObjectName("windowCtrl")
        self.min_btn.setFixedSize(28, 28)
        self.min_btn.setToolTip("最小化")
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("windowCtrl")
        self.max_btn.setFixedSize(28, 28)
        self.max_btn.setToolTip("最大化/还原")
        self.max_btn.clicked.connect(self.toggle_maximize)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("windowCtrlClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self.close)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        # 标题栏拖拽
        bar.mousePressEvent = self._title_bar_mouse_press
        bar.mouseMoveEvent = self._title_bar_mouse_move
        bar.mouseReleaseEvent = self._title_bar_mouse_release
        bar.mouseDoubleClickEvent = lambda event: self.toggle_maximize()

        return bar

    def _build_sidebar(self):
        """构建现代玻璃拟态侧边栏，含几何极简看板娘。"""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 24)
        sidebar_layout.setSpacing(10)

        # 顶部简洁装饰线
        decor = QFrame()
        decor.setObjectName("sidebarDecor")
        decor.setFixedHeight(3)
        sidebar_layout.addWidget(decor)
        sidebar_layout.addSpacing(8)

        logo = QLabel("BiliDown")
        logo.setObjectName("logo")
        subtitle = QLabel("Bilibili 视频下载器")
        subtitle.setObjectName("logo-subtitle")
        sidebar_layout.addWidget(logo)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(24)

        self.nav_download_btn = QPushButton(" 下 载")
        self.nav_download_btn.setObjectName("navBtn")
        self.nav_download_btn.setCheckable(True)
        self.nav_download_btn.setChecked(True)
        self.nav_download_btn.clicked.connect(lambda: self.switch_page(0))
        sidebar_layout.addWidget(self.nav_download_btn)

        self.nav_history_btn = QPushButton(" 历 史")
        self.nav_history_btn.setObjectName("navBtn")
        self.nav_history_btn.setCheckable(True)
        self.nav_history_btn.clicked.connect(lambda: self.switch_page(1))
        sidebar_layout.addWidget(self.nav_history_btn)

        self.nav_settings_btn = QPushButton(" 设 置")
        self.nav_settings_btn.setObjectName("navBtn")
        self.nav_settings_btn.setCheckable(True)
        self.nav_settings_btn.clicked.connect(lambda: self.switch_page(2))
        sidebar_layout.addWidget(self.nav_settings_btn)

        sidebar_layout.addStretch()

        # 看板娘 + 对话气泡
        mascot_box = QFrame()
        mascot_box.setObjectName("mascotBox")
        mascot_layout = QVBoxLayout(mascot_box)
        mascot_layout.setContentsMargins(10, 10, 10, 10)
        mascot_layout.setSpacing(6)

        self.mascot_bubble = QLabel("粘贴链接，开始下载")
        self.mascot_bubble.setObjectName("mascotBubble")
        self.mascot_bubble.setWordWrap(True)
        self.mascot_bubble.setAlignment(Qt.AlignCenter)
        mascot_layout.addWidget(self.mascot_bubble)

        mascot = QLabel()
        mascot.setObjectName("mascot")
        mascot.setAlignment(Qt.AlignCenter)
        if self.mascot_image_path.exists():
            pixmap = QPixmap(str(self.mascot_image_path)).scaled(
                150, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            mascot.setPixmap(pixmap)
        else:
            mascot.setText("Assistant")
        mascot_layout.addWidget(mascot)

        sidebar_layout.addWidget(mascot_box)
        sidebar_layout.addSpacing(10)

        tip = QLabel("简洁、干净、好用")
        tip.setObjectName("sidebar-tip")
        tip.setWordWrap(True)
        sidebar_layout.addWidget(tip)

        return sidebar

    def _title_bar_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def _title_bar_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def _title_bar_mouse_release(self, event):
        self._drag_pos = None
        event.accept()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def resizeEvent(self, event):
        if self.sakura_overlay and self.body_widget:
            self.sakura_overlay.setGeometry(self.body_widget.rect())
        super().resizeEvent(event)

    # ---------- 下载页 ----------

    def _build_download_page(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("下载任务")
        header.setObjectName("page-title")
        sub = QLabel("粘贴链接，选择格式，一键下载")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # 输入卡片
        input_card, input_layout = self.create_card("链接输入")
        self.input_edit = DroppablePlainTextEdit()
        self.input_edit.setPlaceholderText(
            "每行一个链接或 BV 号，也可以用空格/逗号分隔\n"
            "支持从浏览器直接拖拽链接到此处\n"
            "示例：https://www.bilibili.com/video/BV13x41117TL"
        )
        self.input_edit.setMinimumHeight(80)
        self.input_edit.textChanged.connect(self.schedule_preview)
        input_layout.addWidget(self.input_edit)

        input_actions = QHBoxLayout()
        self.preview_btn = QPushButton("解析一下")
        self.preview_btn.clicked.connect(lambda: self.start_preview(force=True))
        self.use_format_btn = QPushButton("使用这个格式")
        self.use_format_btn.setEnabled(False)
        self.use_format_btn.clicked.connect(self.use_selected_format)
        self.add_pages_btn = QPushButton("添加选中的分P")
        self.add_pages_btn.setEnabled(False)
        self.add_pages_btn.clicked.connect(self.add_selected_pages_to_input)
        self.select_all_pages_btn = QPushButton("全选分P")
        self.select_all_pages_btn.setEnabled(False)
        self.select_all_pages_btn.clicked.connect(lambda: self.pages_table.selectAll())
        self.clear_input_btn = QPushButton("清空输入")
        self.clear_input_btn.setObjectName("secondaryBtn")
        self.clear_input_btn.clicked.connect(self.clear_input)
        self.import_links_btn = QPushButton("导入链接")
        self.import_links_btn.setObjectName("secondaryBtn")
        self.import_links_btn.clicked.connect(self.import_links_from_file)
        input_actions.addWidget(self.preview_btn)
        input_actions.addWidget(self.use_format_btn)
        input_actions.addWidget(self.add_pages_btn)
        input_actions.addWidget(self.select_all_pages_btn)
        input_actions.addStretch()
        input_actions.addWidget(self.import_links_btn)
        input_actions.addWidget(self.clear_input_btn)
        input_layout.addLayout(input_actions)
        layout.addWidget(input_card)

        # 预览卡片
        preview_card, preview_layout = self.create_card("视频预览")
        preview_top = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setObjectName("cover")
        self.cover_label.setFixedSize(220, 124)
        self.cover_label.setAlignment(Qt.AlignCenter)
        preview_top.addWidget(self.cover_label)

        meta_grid = QGridLayout()
        meta_grid.setColumnStretch(1, 1)
        meta_grid.setColumnStretch(3, 1)
        meta_grid.setHorizontalSpacing(12)
        meta_grid.setVerticalSpacing(6)
        self.preview_title_label = QLabel("粘贴链接后自动解析")
        self.preview_title_label.setWordWrap(True)
        self.preview_title_label.setStyleSheet("font-weight: 600; color: #1a1a2e;")
        self.preview_uploader_label = QLabel("-")
        self.preview_duration_label = QLabel("-")
        self.preview_stats_label = QLabel("-")
        self.preview_pages_label = QLabel("-")
        self.preview_source_label = QLabel("-")
        self.preview_url_label = QLabel("-")
        self.preview_url_label.setWordWrap(True)
        self.preview_note_label = QLabel("-")
        self.preview_note_label.setWordWrap(True)
        meta_grid.addWidget(QLabel("标题"), 0, 0)
        meta_grid.addWidget(self.preview_title_label, 0, 1, 1, 3)
        meta_grid.addWidget(QLabel("UP/作者"), 1, 0)
        meta_grid.addWidget(self.preview_uploader_label, 1, 1)
        meta_grid.addWidget(QLabel("时长"), 1, 2)
        meta_grid.addWidget(self.preview_duration_label, 1, 3)
        meta_grid.addWidget(QLabel("数据"), 2, 0)
        meta_grid.addWidget(self.preview_stats_label, 2, 1)
        meta_grid.addWidget(QLabel("分P"), 2, 2)
        meta_grid.addWidget(self.preview_pages_label, 2, 3)
        meta_grid.addWidget(QLabel("来源"), 3, 0)
        meta_grid.addWidget(self.preview_source_label, 3, 1)
        meta_grid.addWidget(QLabel("链接"), 3, 2)
        meta_grid.addWidget(self.preview_url_label, 4, 0, 1, 4)
        meta_grid.addWidget(self.preview_note_label, 5, 0, 1, 4)
        preview_top.addLayout(meta_grid, 1)
        preview_layout.addLayout(preview_top)

        self.formats_table = QTableWidget(0, 8)
        self.formats_table.setHorizontalHeaderLabels(
            ["选择", "格式ID", "类型", "分辨率", "FPS", "编码", "大小", "说明"]
        )
        self.formats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.formats_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.formats_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formats_table.itemSelectionChanged.connect(self.on_format_selection_changed)
        self.formats_table.setMinimumHeight(120)
        preview_layout.addWidget(self.formats_table)

        pages_label = QLabel("分 P 列表（多 P 视频可勾选要下载的分 P）")
        pages_label.setStyleSheet("color: #6b7280; margin-top: 4px;")
        preview_layout.addWidget(pages_label)
        self.pages_table = QTableWidget(0, 4)
        self.pages_table.setHorizontalHeaderLabels(["分P", "标题", "时长", "链接"])
        self.pages_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.pages_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.pages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pages_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.pages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pages_table.setMinimumHeight(80)
        self.pages_table.setVisible(False)
        pages_label.setVisible(False)
        self.pages_label = pages_label
        preview_layout.addWidget(self.pages_table)
        layout.addWidget(preview_card)

        # 队列卡片
        queue_card, queue_layout = self.create_card("⏬ 下载队列")
        action_row = QHBoxLayout()
        self.start_btn = QPushButton("开始下载吧")
        if (RESOURCE_DIR / "download.png").exists():
            self.start_btn.setIcon(QIcon(str(RESOURCE_DIR / "download.png")))
        self.start_btn.clicked.connect(self.start_downloads)
        self.pause_btn = QPushButton("暂停一下")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause_queue)
        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_downloads)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.pause_btn)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()
        queue_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        queue_layout.addWidget(self.progress_bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["任务", "状态", "进度", "输出"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_task_context_menu)
        self.table.doubleClicked.connect(self.on_task_double_clicked)
        self.table.setMinimumHeight(120)
        queue_layout.addWidget(self.table)

        log_header = QHBoxLayout()
        log_label = QLabel("运行日志")
        log_label.setStyleSheet("font-weight: 600; color: #1a1a2e;")
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("secondaryBtn")
        self.clear_log_btn.clicked.connect(self.log_edit_clear)
        log_header.addWidget(log_label)
        log_header.addStretch()
        log_header.addWidget(self.clear_log_btn)
        queue_layout.addLayout(log_header)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(60)
        self.log_edit.setMaximumHeight(140)
        queue_layout.addWidget(self.log_edit)
        layout.addWidget(queue_card)

        layout.addStretch()
        scroll.setWidget(container)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self.content_stack.addWidget(page)

    # ---------- 历史页 ----------

    def _build_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("下载历史")
        header.setObjectName("page-title")
        sub = QLabel("查看、重新下载或管理已完成任务")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        action_row = QHBoxLayout()
        self.history_refresh_btn = QPushButton("刷新")
        self.history_refresh_btn.setObjectName("secondaryBtn")
        self.history_refresh_btn.clicked.connect(self.refresh_history)
        self.history_redownload_btn = QPushButton("重新下载")
        self.history_redownload_btn.clicked.connect(self.redownload_history)
        self.history_open_file_btn = QPushButton("打开文件")
        self.history_open_file_btn.setObjectName("secondaryBtn")
        self.history_open_file_btn.clicked.connect(self.open_history_file)
        self.history_open_dir_btn = QPushButton("打开目录")
        self.history_open_dir_btn.setObjectName("secondaryBtn")
        self.history_open_dir_btn.clicked.connect(self.open_history_dir)
        self.history_delete_btn = QPushButton("删除记录")
        self.history_delete_btn.setObjectName("secondaryBtn")
        self.history_delete_btn.clicked.connect(self.delete_history)
        self.history_clear_btn = QPushButton("清空全部")
        self.history_clear_btn.setObjectName("secondaryBtn")
        self.history_clear_btn.clicked.connect(self.clear_all_history)
        self.history_export_csv_btn = QPushButton("导出 CSV")
        self.history_export_csv_btn.setObjectName("secondaryBtn")
        self.history_export_csv_btn.clicked.connect(lambda: self.export_history("csv"))
        self.history_export_json_btn = QPushButton("导出 JSON")
        self.history_export_json_btn.setObjectName("secondaryBtn")
        self.history_export_json_btn.clicked.connect(lambda: self.export_history("json"))
        action_row.addWidget(self.history_refresh_btn)
        action_row.addWidget(self.history_redownload_btn)
        action_row.addWidget(self.history_open_file_btn)
        action_row.addWidget(self.history_open_dir_btn)
        action_row.addWidget(self.history_delete_btn)
        action_row.addWidget(self.history_clear_btn)
        action_row.addWidget(self.history_export_csv_btn)
        action_row.addWidget(self.history_export_json_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ["标题", "状态", "大小", "时长", "分辨率", "完成时间", "路径"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSortingEnabled(True)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self.show_history_context_menu)
        self.history_table.doubleClicked.connect(self.on_history_double_clicked)
        layout.addWidget(self.history_table, 1)
        self.content_stack.addWidget(page)

    # ---------- 设置页 ----------

    def _build_settings_page(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("偏好设置")
        header.setObjectName("page-title")
        sub = QLabel("按自己的习惯配置下载方式")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # Cookie 卡片
        cookie_card, cookie_layout = self.create_card("Cookie 设置")
        cookie_mode_row = QHBoxLayout()
        cookie_mode_row.addWidget(QLabel("Cookie 来源"))
        self.cookie_mode_combo = QComboBox()
        self.cookie_mode_combo.addItem("不使用 Cookie（公开视频自动兜底）", "none")
        self.cookie_mode_combo.addItem("cookies.txt 文件", "file")
        self.cookie_mode_combo.addItem("读取 Chrome Cookie", "chrome")
        self.cookie_mode_combo.addItem("读取 Edge Cookie", "edge")
        self.cookie_mode_combo.addItem("读取 Firefox Cookie", "firefox")
        self.cookie_mode_combo.currentIndexChanged.connect(self.update_cookie_controls)
        cookie_mode_row.addWidget(self.cookie_mode_combo, 1)
        self.qr_login_btn = QPushButton("扫码登录")
        self.qr_login_btn.setStyleSheet("background: #16a34a;")
        self.qr_login_btn.clicked.connect(self.start_qr_login)
        self.check_cookie_btn = QPushButton("检测一下")
        self.check_cookie_btn.clicked.connect(self.check_cookie_status)
        cookie_mode_row.addWidget(self.qr_login_btn)
        cookie_mode_row.addWidget(self.check_cookie_btn)
        cookie_layout.addLayout(cookie_mode_row)

        cookie_file_row = QHBoxLayout()
        cookie_file_row.addWidget(QLabel("Cookie 文件"))
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("选择从已登录 B站 浏览器导出的 cookies.txt")
        self.cookie_file_btn = QPushButton("浏览")
        self.cookie_file_btn.setObjectName("secondaryBtn")
        self.cookie_file_btn.clicked.connect(self.choose_cookie_file)
        cookie_file_row.addWidget(self.cookie_file_edit, 1)
        cookie_file_row.addWidget(self.cookie_file_btn)
        cookie_layout.addLayout(cookie_file_row)
        layout.addWidget(cookie_card)

        # 下载设置卡片
        dl_card, dl_layout = self.create_card("下载设置")
        dl_grid = QGridLayout(dl_card)
        dl_grid.setColumnStretch(1, 1)
        dl_grid.setColumnStretch(3, 1)
        dl_grid.setHorizontalSpacing(12)
        dl_grid.setVerticalSpacing(10)

        self.quality_combo = QComboBox()
        for value, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("清晰度"), 0, 0)
        dl_grid.addWidget(self.quality_combo, 0, 1)

        self.codec_combo = QComboBox()
        for value, label in CODEC_LABELS.items():
            self.codec_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("编码偏好"), 0, 2)
        dl_grid.addWidget(self.codec_combo, 0, 3)

        self.audio_quality_combo = QComboBox()
        for value, label in AUDIO_QUALITY_LABELS.items():
            self.audio_quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("音频质量"), 1, 0)
        dl_grid.addWidget(self.audio_quality_combo, 1, 1)

        self.thread_combo = QComboBox()
        for value in range(1, 9):
            self.thread_combo.addItem(str(value), value)
        dl_grid.addWidget(QLabel("分片并发"), 1, 2)
        dl_grid.addWidget(self.thread_combo, 1, 3)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 8)
        self.concurrent_spin.setToolTip("同时下载多个视频的任务数（1=顺序下载，建议 2~4）")
        dl_grid.addWidget(QLabel("任务并发"), 4, 0)
        dl_grid.addWidget(self.concurrent_spin, 4, 1)

        self.custom_format_edit = QLineEdit()
        self.custom_format_edit.setReadOnly(True)
        self.custom_format_edit.setPlaceholderText("在预览格式表选中一行后点击\"使用这个格式\"")
        dl_grid.addWidget(QLabel("选中格式"), 2, 0)
        dl_grid.addWidget(self.custom_format_edit, 2, 1, 1, 3)

        dl_grid.addWidget(QLabel("保存位置"), 3, 0)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_btn = QPushButton("浏览")
        self.dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.dir_btn.clicked.connect(self.choose_download_dir)
        self.open_dir_btn = QPushButton("打开")
        self.open_dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.open_dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.open_dir_btn.clicked.connect(self.open_download_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.dir_btn)
        dir_row.addWidget(self.open_dir_btn)
        dl_grid.addLayout(dir_row, 3, 1, 1, 3)
        layout.addWidget(dl_card)

        # 网络与文件名卡片
        net_card, net_layout = self.create_card("网络与文件名")
        net_grid = QGridLayout(net_card)
        net_grid.setColumnStretch(1, 1)
        net_grid.setHorizontalSpacing(12)
        net_grid.setVerticalSpacing(10)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("可选，例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080")
        net_grid.addWidget(QLabel("代理"), 0, 0)
        net_grid.addWidget(self.proxy_edit, 0, 1, 1, 3)
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("%(title).180B [%(id)s].%(ext)s")
        net_grid.addWidget(QLabel("文件名"), 1, 0)
        net_grid.addWidget(self.template_edit, 1, 1, 1, 3)
        layout.addWidget(net_card)

        # 附加内容卡片
        extra_card, extra_layout = self.create_card("附加内容")
        extra_row = QHBoxLayout()
        self.thumbnail_check = QCheckBox("下载封面")
        self.subtitle_check = QCheckBox("下载字幕")
        self.danmaku_check = QCheckBox("下载弹幕(B站)")
        extra_row.addWidget(self.thumbnail_check)
        extra_row.addWidget(self.subtitle_check)
        extra_row.addWidget(self.danmaku_check)
        extra_row.addStretch()
        extra_layout.addLayout(extra_row)
        layout.addWidget(extra_card)

        # 视觉特效开关卡片
        fx_card, fx_layout = self.create_card("视觉特效", glow=True, glow_color="#a78bfa")
        fx_row = QHBoxLayout()
        self.sakura_check = QCheckBox("粒子效果")
        self.neon_check = QCheckBox("卡片发光")
        self.sound_check = QCheckBox("音效反馈")
        self.sakura_check.stateChanged.connect(self.toggle_sakura_overlay)
        self.neon_check.stateChanged.connect(self.apply_fx_settings)
        self.sound_check.stateChanged.connect(self.apply_fx_settings)
        fx_row.addWidget(self.sakura_check)
        fx_row.addWidget(self.neon_check)
        fx_row.addWidget(self.sound_check)
        fx_row.addStretch()
        fx_layout.addLayout(fx_row)
        layout.addWidget(fx_card)

        # 恢复默认设置按钮
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        self.export_settings_btn = QPushButton("导出设置")
        self.export_settings_btn.setObjectName("secondaryBtn")
        self.export_settings_btn.clicked.connect(self.export_settings)
        reset_layout.addWidget(self.export_settings_btn)
        self.import_settings_btn = QPushButton("导入设置")
        self.import_settings_btn.setObjectName("secondaryBtn")
        self.import_settings_btn.clicked.connect(self.import_settings)
        reset_layout.addWidget(self.import_settings_btn)
        self.reset_settings_btn = QPushButton("恢复默认设置")
        self.reset_settings_btn.setObjectName("secondaryBtn")
        self.reset_settings_btn.clicked.connect(self.reset_settings)
        reset_layout.addWidget(self.reset_settings_btn)
        layout.addLayout(reset_layout)

        layout.addStretch()
        scroll.setWidget(container)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self.content_stack.addWidget(page)

    # ---------- 主题样式 ----------

    def _apply_theme(self):
        self.setStyleSheet("""
            QWidget {
                font-size: 14px;
                font-family: "Microsoft YaHei", "Segoe UI", "Inter", sans-serif;
                color: #334155;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #f8fafc, stop:0.5 #f1f5f9, stop:1 #f5f3ff);
            }
            #sidebar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e293b, stop:1 #334155);
                border-right: 1px solid rgba(255,255,255,0.08);
            }
            #sidebarDecor {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #fb923c, stop:0.5 #f472b6, stop:1 #a78bfa);
                border-radius: 2px;
            }
            #logo {
                font-size: 26px;
                font-weight: 800;
                color: #f8fafc;
                padding-left: 4px;
                letter-spacing: -0.5px;
            }
            #logo-subtitle {
                font-size: 12px;
                color: rgba(248,250,252,0.65);
                padding-left: 4px;
                letter-spacing: 1px;
            }
            #navBtn {
                background: transparent;
                color: rgba(248,250,252,0.85);
                border: none;
                border-radius: 14px;
                padding: 12px 18px;
                text-align: left;
                font-size: 15px;
                font-weight: 500;
                margin: 4px 0;
            }
            #navBtn:checked {
                background: rgba(255,255,255,0.12);
                color: #ffffff;
                font-weight: 600;
                border: 1px solid rgba(255,255,255,0.15);
            }
            #navBtn:hover:!checked {
                background: rgba(255,255,255,0.08);
            }
            #sidebar-tip {
                font-size: 12px;
                color: rgba(248,250,252,0.55);
                padding: 10px 8px;
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #titleBar {
                background: rgba(255,255,255,0.85);
                border-bottom: 1px solid rgba(148,163,184,0.2);
            }
            #titleIcon {
                background: transparent;
                border-radius: 8px;
            }
            #titleLabel {
                color: #334155;
                font-size: 14px;
                font-weight: 600;
                background: transparent;
            }
            #windowCtrl {
                background: transparent;
                color: #64748b;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 500;
                padding: 0;
            }
            #windowCtrl:hover {
                background: #e2e8f0;
                color: #334155;
            }
            #windowCtrlClose {
                background: transparent;
                color: #64748b;
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: 500;
                padding: 0;
            }
            #windowCtrlClose:hover {
                background: #fecaca;
                color: #dc2626;
            }
            #mascotBox {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 20px;
            }
            #mascot {
                font-size: 12px;
                font-weight: 600;
                color: rgba(248,250,252,0.7);
                background: transparent;
                border-radius: 14px;
                padding: 6px 4px;
                min-height: 60px;
            }
            #mascotBubble {
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
                background: rgba(255,255,255,0.92);
                border: none;
                border-radius: 12px;
                padding: 8px 10px;
            }
            #page-title {
                font-size: 24px;
                font-weight: 700;
                color: #1e293b;
                padding-bottom: 4px;
            }
            #page-subtitle {
                color: #64748b;
                margin-bottom: 6px;
                font-size: 13px;
                font-weight: 500;
            }
            #card {
                background: rgba(255,255,255,0.82);
                border: 1px solid rgba(148,163,184,0.18);
                border-radius: 20px;
            }
            #card-title {
                font-size: 15px;
                font-weight: 700;
                color: #334155;
                padding-bottom: 8px;
                border-bottom: 1px solid rgba(148,163,184,0.18);
            }
            #cover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fff7ed, stop:0.5 #fdf4ff, stop:1 #f0f9ff);
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 16px;
                color: #64748b;
                font-weight: 500;
            }
            QPlainTextEdit, QLineEdit, QComboBox {
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(148,163,184,0.25);
                border-radius: 12px;
                padding: 8px 12px;
                selection-background-color: #fb923c;
                selection-color: white;
            }
            QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {
                border: 1px solid #fb923c;
                background: rgba(255,255,255,0.95);
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #94a3b8;
                width: 0px;
                height: 0px;
            }
            QPushButton {
                background: #334155;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 22px;
                font-weight: 600;
            }
            QPushButton:hover:!disabled {
                background: #475569;
            }
            QPushButton:pressed:!disabled {
                background: #1e293b;
            }
            QPushButton:disabled {
                background: #e2e8f0;
                color: #94a3b8;
            }
            #secondaryBtn {
                background: rgba(255,255,255,0.7);
                color: #334155;
                border: 1px solid rgba(148,163,184,0.25);
            }
            #secondaryBtn:hover:!disabled {
                background: rgba(255,255,255,0.95);
                border: 1px solid rgba(148,163,184,0.4);
            }
            QProgressBar {
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 12px;
                height: 22px;
                background: rgba(255,255,255,0.6);
                text-align: center;
                color: #334155;
                font-weight: 600;
                font-size: 12px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #fb923c, stop:1 #a78bfa);
                border-radius: 10px;
            }
            QTableWidget {
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(148,163,184,0.18);
                border-radius: 16px;
                gridline-color: rgba(148,163,184,0.12);
                selection-background-color: rgba(251,146,60,0.2);
                selection-color: #1e293b;
                alternate-background-color: rgba(248,250,252,0.5);
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background: rgba(241,245,249,0.8);
                color: #475569;
                border: none;
                padding: 10px 8px;
                font-weight: 700;
            }
            QCheckBox {
                spacing: 8px;
                font-weight: 500;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
                border-radius: 6px;
                border: 1px solid rgba(148,163,184,0.4);
                background: rgba(255,255,255,0.7);
            }
            QCheckBox::indicator:checked {
                background: #fb923c;
                border: 1px solid #fb923c;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                border: none;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(148,163,184,0.35);
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(148,163,184,0.55);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: none;
                border: none;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                border: none;
                margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(148,163,184,0.35);
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(148,163,184,0.55);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: none;
                border: none;
            }
            QStatusBar {
                background: rgba(255,255,255,0.7);
                color: #475569;
                border-top: 1px solid rgba(148,163,184,0.15);
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QStatusBar::item {
                border: none;
            }
            QLabel {
                color: #334155;
                background: transparent;
            }
            QMenu {
                background: rgba(255,255,255,0.95);
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 26px;
                border-radius: 8px;
                font-weight: 500;
            }
            QMenu::item:selected {
                background: rgba(251,146,60,0.15);
                color: #1e293b;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(148,163,184,0.18);
                margin: 5px 10px;
            }
            QToolTip {
                background: #1e293b;
                color: #f8fafc;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 500;
            }
        """)

    # ---------- 设置读写 ----------

    def apply_settings_to_ui(self):
        self.dir_edit.setText(self.settings["download_dir"])
        self.proxy_edit.setText(self.settings.get("proxy", ""))
        self.cookie_file_edit.setText(self.settings.get("cookie_file", ""))
        self.template_edit.setText(self.settings.get("filename_template", DEFAULT_SETTINGS["filename_template"]))
        self.custom_format_edit.setText(self.settings.get("custom_format", ""))
        self.set_combo_value(self.quality_combo, self.settings.get("quality", "best"))
        self.set_combo_value(self.cookie_mode_combo, self.settings.get("cookie_mode", "none"))
        self.set_combo_value(self.thread_combo, int(self.settings.get("fragment_threads", 4)))
        self.set_combo_value(self.codec_combo, self.settings.get("codec_preference", "auto"))
        self.set_combo_value(self.audio_quality_combo, self.settings.get("audio_quality", "auto"))
        self.thumbnail_check.setChecked(bool(self.settings.get("download_thumbnail", False)))
        self.subtitle_check.setChecked(bool(self.settings.get("download_subtitle", False)))
        self.danmaku_check.setChecked(bool(self.settings.get("download_danmaku", False)))
        self.sakura_check.setChecked(bool(self.settings.get("fx_sakura", True)))
        self.neon_check.setChecked(bool(self.settings.get("fx_neon", True)))
        self.sound_check.setChecked(bool(self.settings.get("fx_sound", True)))
        self.apply_fx_settings()
        self.update_cookie_controls()

    def set_combo_value(self, combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def collect_settings(self):
        settings = dict(self.settings)
        settings.update({
            "download_dir": self.dir_edit.text().strip() or str(DEFAULT_DOWNLOAD_DIR),
            "quality": self.quality_combo.currentData(),
            "custom_format": self.custom_format_edit.text().strip(),
            "cookie_mode": self.cookie_mode_combo.currentData(),
            "cookie_file": self.cookie_file_edit.text().strip(),
            "proxy": self.proxy_edit.text().strip(),
            "filename_template": self.template_edit.text().strip() or DEFAULT_SETTINGS["filename_template"],
            "fragment_threads": self.thread_combo.currentData(),
            "concurrent_downloads": self.concurrent_spin.value(),
            "codec_preference": self.codec_combo.currentData(),
            "audio_quality": self.audio_quality_combo.currentData(),
            "download_thumbnail": self.thumbnail_check.isChecked(),
            "download_subtitle": self.subtitle_check.isChecked(),
            "download_danmaku": self.danmaku_check.isChecked(),
            "fx_sakura": self.sakura_check.isChecked(),
            "fx_neon": self.neon_check.isChecked(),
            "fx_sound": self.sound_check.isChecked(),
        })
        return settings

    def reset_settings(self):
        """恢复默认设置并更新 UI。"""
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "确定要恢复默认设置吗？\n当前自定义的下载目录、Cookie 等配置将被重置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.settings = dict(DEFAULT_SETTINGS)
        ok, err = save_settings(self.settings)
        if not ok:
            QMessageBox.warning(self, "保存失败", f"恢复默认设置后保存失败：\n{err}")
            return
        self.apply_settings_to_ui()
        self.statusBar().showMessage("已恢复默认设置", 5000)

    def toggle_sakura_overlay(self, state):
        """开关樱花飘落层。"""
        if self.sakura_overlay is None:
            return
        self.sakura_overlay.setVisible(state == Qt.Checked)

    def apply_fx_settings(self):
        """应用二次元特效开关到运行态。"""
        if self.sound_player:
            self.sound_player.enabled = self.sound_check.isChecked()
        neon_enabled = self.neon_check.isChecked()
        for card in self.findChildren(NeonGlowCard):
            card.set_glow_enabled(neon_enabled)

    # ---------- 文件/Cookie 选择 ----------

    def choose_download_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择下载文件夹", self.dir_edit.text())
        if folder:
            self.dir_edit.setText(folder)

    def choose_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", "",
            "Cookie Files (cookies.txt *.txt);;All Files (*.*)",
        )
        if path:
            self.cookie_file_edit.setText(path)
            self.set_combo_value(self.cookie_mode_combo, "file")

    def update_cookie_controls(self):
        use_file = self.cookie_mode_combo.currentData() == "file"
        self.cookie_file_edit.setEnabled(use_file)
        self.cookie_file_btn.setEnabled(use_file)

    def open_download_dir(self):
        open_path_in_explorer(self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR))

    # ---------- Cookie 检测 ----------

    def check_cookie_status(self):
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            return
        self.settings = self.collect_settings()
        self.check_cookie_btn.setEnabled(False)
        self.statusBar().showMessage("正在检测 Cookie...")
        self.cookie_check_worker = CookieCheckWorker(self.settings, self)
        self.cookie_check_worker.result_ready.connect(self.on_cookie_check_result)
        self.cookie_check_worker.failed.connect(self.on_cookie_check_failed)
        self.cookie_check_worker.finished.connect(self.on_cookie_check_finished)
        self.cookie_check_worker.start()

    def on_cookie_check_result(self, result):
        if result.get("reason"):
            QMessageBox.warning(self, "Cookie 检测", result["reason"])
            return
        if not result.get("logged_in"):
            QMessageBox.information(
                self, "Cookie 检测",
                "Cookie 未登录或已失效。\n请重新导出 cookies.txt 或关闭浏览器后重试。",
            )
            return
        vip_text = ""
        if result.get("vip_status"):
            vip_type = result.get("vip_type")
            if vip_type == 2:
                vip_text = "（年度大会员）"
            elif vip_type == 1:
                vip_text = "（月度大会员）"
        msg = (
            f"Cookie 可用\n\n"
            f"用户名: {result.get('username') or '-'}\n"
            f"UID: {result.get('mid') or '-'}\n"
            f"大会员: {'是' + vip_text if result.get('vip_status') else '否'}\n"
            f"SESSDATA: {'已获取' if result.get('sessdata_found') else '未获取'}"
        )
        QMessageBox.information(self, "Cookie 检测", msg)

    def on_cookie_check_failed(self, msg):
        QMessageBox.warning(self, "Cookie 检测失败", msg)

    def on_cookie_check_finished(self):
        self.check_cookie_btn.setEnabled(True)
        self.statusBar().showMessage("Cookie 检测完成", 5000)

    def start_qr_login(self):
        """小白一键配置：扫码登录 B站，自动保存 cookies.txt。"""
        self.settings = self.collect_settings()
        dialog = QrLoginDialog(self.settings, self)
        dialog.start()
        if dialog.exec_() == QDialog.Accepted and dialog.cookie_file_path:
            self.cookie_file_edit.setText(dialog.cookie_file_path)
            self.set_combo_value(self.cookie_mode_combo, "file")
            self.update_cookie_controls()
            self.settings = self.collect_settings()
            ok, err = save_settings(self.settings)
            if not ok:
                self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            self.append_log(f"扫码登录成功，Cookie 已保存: {dialog.cookie_file_path}")
            self.statusBar().showMessage("扫码登录成功", 5000)
            self.check_cookie_status()

    # ---------- 预览 ----------

    def schedule_preview(self):
        if self.worker and self.worker.isRunning():
            return
        self.preview_pending = True
        self.preview_timer.start(800)

    def start_preview(self, force=False):
        if self.worker and self.worker.isRunning():
            return
        if self.sound_player:
            self.sound_player.play("click")
        text = self.input_edit.toPlainText()
        urls = split_inputs(text)
        if not urls:
            self.clear_preview()
            return
        url = urls[0]
        if not force and not self.preview_pending:
            return
        self.preview_pending = False
        self.preview_request_id += 1
        rid = self.preview_request_id
        self.preview_btn.setEnabled(False)
        self.preview_title_label.setText("解析中...")
        self.preview_note_label.setText("如果是批量链接，这里预览第一个。")
        self.formats_table.setRowCount(0)
        self.use_format_btn.setEnabled(False)
        self.preview_worker = PreviewWorker(rid, url, self.collect_settings(), self)
        self.preview_worker.info_ready.connect(self.on_preview_ready)
        self.preview_worker.failed.connect(self.on_preview_failed)
        self.preview_worker.finished.connect(self.on_preview_finished)
        self.preview_worker.start()

    def on_preview_ready(self, request_id, info):
        if request_id != self.preview_request_id:
            return
        if self.sound_player:
            self.sound_player.play("success")
        self.preview_formats = info.get("formats") or []
        self.preview_title_label.setText(info.get("title") or "-")
        self.preview_uploader_label.setText(info.get("uploader") or "-")
        self.preview_duration_label.setText(format_duration(info.get("duration")))
        stats = f"播放 {format_count(info.get('view_count'))}  点赞 {format_count(info.get('like_count'))}"
        self.preview_stats_label.setText(stats)
        pages = info.get("pages") or []
        self.preview_pages_label.setText(f"{len(pages)} P" if pages else "单 P")
        self.preview_source_label.setText(info.get("source") or "-")
        self.preview_url_label.setText(info.get("url") or "-")
        self.preview_note_label.setText(info.get("note") or "-")
        thumb_url = info.get("thumbnail") or ""
        if thumb_url:
            self._load_thumbnail(thumb_url)
        else:
            self.cover_label.setText("暂无封面")
            self.cover_label.setPixmap(QPixmap())
        self.fill_formats_table(self.preview_formats)
        if pages:
            self.fill_pages_table(pages)
        else:
            self.pages_table.setRowCount(0)
            self.pages_table.setVisible(False)
            self.pages_label.setVisible(False)
            self.add_pages_btn.setEnabled(False)
            self.select_all_pages_btn.setEnabled(False)

    def on_preview_failed(self, request_id, msg):
        if request_id != self.preview_request_id:
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self.preview_title_label.setText("解析失败")
        self.preview_note_label.setText(msg)
        QMessageBox.warning(self, "解析失败", f"无法解析该链接：\n\n{msg}\n\n请检查：\n1. 链接是否正确\n2. 是否需要配置 Cookie\n3. 网络是否连接正常")

    def on_preview_finished(self):
        self.preview_btn.setEnabled(True)

    def _load_thumbnail(self, url):
        try:
            proxy = (self.settings.get("proxy") or "").strip()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            resp = requests.get(url, headers=std_headers(), timeout=10, proxies=proxies)
            resp.raise_for_status()
            pixmap = QPixmap()
            pixmap.loadFromData(resp.content)
            if not pixmap.isNull():
                self.cover_label.setPixmap(pixmap.scaled(220, 124, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        except Exception:
            pass
        self.cover_label.setText("封面加载失败")
        self.cover_label.setPixmap(QPixmap())

    def fill_formats_table(self, formats):
        self.formats_table.setRowCount(0)
        if not formats:
            return
        # 找出最高分辨率的视频流和最高码率的音频流，用于推荐标记
        best_video = None
        best_audio = None
        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if vcodec and vcodec != "none":
                if best_video is None or (f.get("height") or 0) > (best_video.get("height") or 0):
                    best_video = f
            if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                if best_audio is None or (f.get("tbr") or 0) > (best_audio.get("tbr") or 0):
                    best_audio = f

        has_cookie = self.settings.get("cookie_mode") != "none"
        has_ffmpeg = FFMPEG_EXE.exists()

        for f in formats:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and is_audio:
                ftype = "音视频"
            elif is_video:
                ftype = "视频"
            elif is_audio:
                ftype = "音频"
            else:
                ftype = "?"
            resolution = ""
            if f.get("width") and f.get("height"):
                resolution = f"{f['width']}x{f['height']}"
            elif f.get("height"):
                resolution = f"{f['height']}p"

            # 推荐标记
            recommend = ""
            if f is best_video and is_video and not is_audio:
                recommend = "推荐"
            elif f is best_audio and is_audio and not is_video:
                recommend = "推荐"

            # 可用性说明
            notes = []
            if f.get("note"):
                notes.append(f["note"])
            # DASH 视频+音频需要 ffmpeg 合并
            if is_video and not is_audio:
                notes.append("需合并音频")
                if not has_ffmpeg:
                    notes.append("缺ffmpeg")
            # 高清格式可能需要 Cookie
            if is_video and (f.get("height") or 0) >= 1080 and not has_cookie:
                notes.append("需Cookie")
            note_text = " | ".join(notes) if notes else ("可直接下载" if (is_video and is_audio) else "")

            self.formats_table.setItem(row, 0, QTableWidgetItem(recommend))
            self.formats_table.setItem(row, 1, QTableWidgetItem(str(f.get("format_id", ""))))
            self.formats_table.setItem(row, 2, QTableWidgetItem(ftype))
            self.formats_table.setItem(row, 3, QTableWidgetItem(resolution))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(f.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(vcodec or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(f.get("filesize"))))
            self.formats_table.setItem(row, 7, QTableWidgetItem(note_text))

        # 增加 DASH 组合推荐行
        if best_video and best_audio and has_ffmpeg:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            combined_size = (best_video.get("filesize") or 0) + (best_audio.get("filesize") or 0)
            vres = ""
            if best_video.get("width") and best_video.get("height"):
                vres = f"{best_video['width']}x{best_video['height']}"
            elif best_video.get("height"):
                vres = f"{best_video['height']}p"
            self.formats_table.setItem(row, 0, QTableWidgetItem("最佳组合"))
            self.formats_table.setItem(row, 1, QTableWidgetItem(f"{best_video.get('format_id','')}+{best_audio.get('format_id','')}"))
            self.formats_table.setItem(row, 2, QTableWidgetItem("DASH组合"))
            self.formats_table.setItem(row, 3, QTableWidgetItem(vres))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(best_video.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(best_video.get("vcodec") or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(combined_size)))
            self.formats_table.setItem(row, 7, QTableWidgetItem("视频+音频合并 | 需ffmpeg"))

    def fill_pages_table(self, pages):
        self.pages_table.setRowCount(0)
        for p in pages:
            row = self.pages_table.rowCount()
            self.pages_table.insertRow(row)
            self.pages_table.setItem(row, 0, QTableWidgetItem(str(p.get("page", 1))))
            self.pages_table.setItem(row, 1, QTableWidgetItem(p.get("title") or "-"))
            self.pages_table.setItem(row, 2, QTableWidgetItem(format_duration(p.get("duration"))))
            self.pages_table.setItem(row, 3, QTableWidgetItem(p.get("url") or "-"))
        self.pages_table.setVisible(True)
        self.pages_label.setVisible(True)
        self.add_pages_btn.setEnabled(True)
        self.select_all_pages_btn.setEnabled(True)

    def clear_preview(self):
        self.preview_title_label.setText("粘贴链接后自动解析")
        self.preview_uploader_label.setText("-")
        self.preview_duration_label.setText("-")
        self.preview_stats_label.setText("-")
        self.preview_pages_label.setText("-")
        self.preview_source_label.setText("-")
        self.preview_url_label.setText("-")
        self.preview_note_label.setText("-")
        self.cover_label.setText("暂无封面")
        self.cover_label.setPixmap(QPixmap())
        self.formats_table.setRowCount(0)
        self.pages_table.setRowCount(0)
        self.pages_table.setVisible(False)
        self.pages_label.setVisible(False)
        self.use_format_btn.setEnabled(False)
        self.add_pages_btn.setEnabled(False)
        self.select_all_pages_btn.setEnabled(False)

    def on_format_selection_changed(self):
        selected = self.formats_table.selectionModel().selectedRows()
        self.use_format_btn.setEnabled(bool(selected))

    def use_selected_format(self):
        selected = self.formats_table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        format_id = self.formats_table.item(row, 1).text()
        self.custom_format_edit.setText(format_id)
        self.append_log(f"已选择格式: {format_id}")

    def add_selected_pages_to_input(self):
        selected_rows = sorted({idx.row() for idx in self.pages_table.selectionModel().selectedRows()})
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先在分 P 列表中选择要下载的分 P。")
            return
        urls = []
        for r in selected_rows:
            url_item = self.pages_table.item(r, 3)
            if url_item and url_item.text().strip():
                urls.append(url_item.text().strip())
        if not urls:
            return
        existing = self.input_edit.toPlainText().strip()
        if existing:
            new_text = existing + "\n" + "\n".join(urls)
        else:
            new_text = "\n".join(urls)
        self.input_edit.setPlainText(new_text)
        self.statusBar().showMessage(f"已添加 {len(urls)} 个分 P", 3000)

    def clear_input(self):
        self.input_edit.clear()
        self.clear_preview()
        self.custom_format_edit.clear()
        self.statusBar().showMessage("已清空输入", 2000)

    def import_links_from_file(self):
        """从 .txt 文件导入链接列表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入链接文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            urls = split_inputs(content)
            if not urls:
                QMessageBox.information(self, "提示", "文件中未找到有效链接。")
                return
            existing = self.input_edit.toPlainText().strip()
            if existing:
                self.input_edit.setPlainText(existing + "\n" + "\n".join(urls))
            else:
                self.input_edit.setPlainText("\n".join(urls))
            self.statusBar().showMessage(f"已导入 {len(urls)} 条链接", 3000)
            self.append_log(f"从文件导入 {len(urls)} 条链接: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"读取文件失败：\n{format_error(exc)}")

    # ---------- 下载 ----------

    def check_cookie_settings(self):
        mode = self.settings.get("cookie_mode") or "none"
        if mode == "file":
            cookie_file = (self.settings.get("cookie_file") or "").strip()
            if not cookie_file:
                return "Cookie 模式为 cookies.txt，但未选择文件。"
            if not Path(cookie_file).exists():
                return f"Cookie 文件不存在: {cookie_file}"
        elif mode in ("chrome", "edge", "firefox"):
            pass
        return ""

    def start_downloads(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前已有下载任务在进行。")
            return
        if self.sound_player:
            self.sound_player.play("start")
        text = self.input_edit.toPlainText()
        urls = split_inputs(text)
        if not urls:
            QMessageBox.information(self, "提示", "请输入要下载的链接。")
            return
        self.settings = self.collect_settings()
        ok, err = save_settings(self.settings)
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        download_dir = self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR
        try:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, "目录创建失败", f"无法创建下载目录：\n{download_dir}\n\n{e}")
            return
        cookie_err = self.check_cookie_settings()
        if cookie_err:
            ret = QMessageBox.question(
                self, "Cookie 提示",
                f"{cookie_err}\n\n是否仍然继续下载？（公开视频可走兜底接口）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for i, url in enumerate(urls):
            url = normalize_input(url)
            self.table.insertRow(i)
            self.set_cell(i, 0, url)
            self.set_cell(i, 1, "等待")
            self.set_cell(i, 2, "0%")
            self.set_cell(i, 3, "")
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.set_controls_enabled(False)
        self.progress_bar.setValue(0)
        self.statusBar().showMessage("下载中...")
        self.worker = DownloadWorker(urls, self.settings, self)
        self.worker.item_started.connect(self.on_item_started)
        self.worker.item_progress.connect(self.on_item_progress)
        self.worker.item_finished.connect(self.on_item_finished)
        self.worker.item_failed.connect(self.on_item_failed)
        self.worker.item_history.connect(self.on_item_history)
        self.worker.log.connect(self.append_log)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.paused_changed.connect(self.on_queue_paused_changed)
        self.worker.start()

    def set_controls_enabled(self, enabled):
        self.input_edit.setEnabled(enabled)
        self.preview_btn.setEnabled(enabled)
        self.use_format_btn.setEnabled(enabled and bool(self.formats_table.selectionModel().selectedRows()))
        self.add_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.select_all_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.dir_btn.setEnabled(enabled)
        self.cookie_mode_combo.setEnabled(enabled)
        self.cookie_file_btn.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.codec_combo.setEnabled(enabled)
        self.audio_quality_combo.setEnabled(enabled)
        self.thread_combo.setEnabled(enabled)
        self.concurrent_spin.setEnabled(enabled)
        self.proxy_edit.setEnabled(enabled)
        self.template_edit.setEnabled(enabled)
        self.thumbnail_check.setEnabled(enabled)
        self.subtitle_check.setEnabled(enabled)
        self.danmaku_check.setEnabled(enabled)
        self.qr_login_btn.setEnabled(enabled)
        self.check_cookie_btn.setEnabled(enabled)

    def set_cell(self, row, col, text):
        item = self.table.item(row, col)
        if item:
            item.setText(text)
        else:
            self.table.setItem(row, col, QTableWidgetItem(text))

    def on_item_started(self, index, url):
        if index >= self.table.rowCount():
            return
        self.set_cell(index, 0, url)
        self.set_cell(index, 1, "下载中")
        self.set_cell(index, 2, "0%")
        self.statusBar().showMessage(f"下载中: {url}")
        self.update_window_title()

    def on_item_progress(self, index, percent, detail):
        if index >= self.table.rowCount():
            return
        self.set_cell(index, 2, detail)
        if percent >= 0:
            # 计算总进度 = 已完成任务数/总数 + 当前任务百分比/总数
            total = self.table.rowCount() or 1
            finished = sum(1 for r in range(total) if self.table.item(r, 1) and self.table.item(r, 1).text() in ("完成", "完成（公开视频兜底）"))
            current = max(0.0, min(100.0, percent)) / 100.0
            overall = (finished + current) / total * 100
            self.progress_bar.setValue(int(overall))
            self.update_window_title()

    def on_item_finished(self, index, output_detail, status_text):
        if index >= self.table.rowCount():
            return
        if self.sound_player and (status_text or "完成") == "完成":
            self.sound_player.play("success2")
        self.set_cell(index, 1, status_text or "完成")
        self.set_cell(index, 2, "100%")
        self.set_cell(index, 3, output_detail)
        self._update_progress_bar()
        self.update_window_title()

    def on_item_failed(self, index, error):
        if index >= self.table.rowCount():
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self.set_cell(index, 1, "失败")
        self.set_cell(index, 2, error)
        self._update_progress_bar()
        self.update_window_title()

    def _update_progress_bar(self):
        total = self.table.rowCount() or 1
        finished = sum(1 for r in range(total) if self.table.item(r, 1) and self.table.item(r, 1).text() in ("完成", "完成（公开视频兜底）", "失败"))
        overall = finished / total * 100
        self.progress_bar.setValue(int(overall))

    def update_window_title(self):
        """根据当前下载状态更新窗口标题和标题栏标签。"""
        total = self.table.rowCount()
        if not total:
            title = self.base_window_title
            self.setWindowTitle(title)
            if hasattr(self, "title_label"):
                self.title_label.setText(title)
            self._update_mascot_by_state("idle")
            return
        running = self.worker and self.worker.isRunning() and not self.worker.cancelled
        if running:
            done = sum(1 for r in range(total) if self.table.item(r, 1) and self.table.item(r, 1).text() in ("完成", "完成（公开视频兜底）", "失败"))
            if self.worker.paused:
                title = f"{self.base_window_title} - 已暂停 ({done}/{total})"
                self._update_mascot_by_state("paused")
            else:
                title = f"{self.base_window_title} - 下载中 ({done}/{total})"
                self._update_mascot_by_state("downloading")
            self.setWindowTitle(title)
            if hasattr(self, "title_label"):
                self.title_label.setText(title)
            return
        failed = sum(1 for r in range(total) if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败")
        cancelled = sum(1 for r in range(total) if self.table.item(r, 1) and self.table.item(r, 1).text() == "已取消")
        if cancelled:
            title = f"{self.base_window_title} - 已取消"
            self._update_mascot_by_state("cancelled")
        elif failed:
            title = f"{self.base_window_title} - 完成（有失败）"
            self._update_mascot_by_state("failed")
        else:
            title = f"{self.base_window_title} - 全部完成"
            self._update_mascot_by_state("completed")
        self.setWindowTitle(title)
        if hasattr(self, "title_label"):
            self.title_label.setText(title)

    def _update_mascot_by_state(self, state):
        """根据状态切换看板娘气泡台词。"""
        if not hasattr(self, "mascot_bubble"):
            return
        lines = {
            "idle": ["粘贴链接，开始下载", "支持 B站、yt-dlp 源", "准备就绪"],
            "downloading": ["下载进行中...", "稍等片刻", "正在处理"],
            "paused": ["已暂停", "随时可以继续", "休息一下"],
            "completed": ["全部完成", "可以去查看了", "下载结束"],
            "failed": ["有任务失败", "检查链接或 Cookie", "可尝试重试"],
            "cancelled": ["已取消", "欢迎再次使用", "操作已中止"],
        }
        self.mascot_bubble.setText(random.choice(lines.get(state, lines["idle"])))

    def on_item_history(self, record):
        append_history_record(record)
        self.history_records = load_history()
        self.refresh_history()

    def on_all_done(self, ok):
        if self.sound_player:
            self.sound_player.play("complete" if ok else "fail")
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setText("暂停一下")
        self.set_controls_enabled(True)
        self.table.setSortingEnabled(True)
        self.update_window_title()
        # 打开目录：取第一个成功任务的目录
        output_dir = ""
        for r in range(self.table.rowCount()):
            detail = self.table.item(r, 3)
            if detail and detail.text():
                p = Path(detail.text())
                if p.exists():
                    output_dir = str(p.parent)
                    break
        if not output_dir:
            output_dir = self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR
        if ok:
            self.progress_bar.setValue(100)
            self.statusBar().showMessage("全部任务完成")
            self.show_tray_message("下载完成", "全部任务已完成")
            reply = QMessageBox.question(
                self, "下载完成",
                f"全部任务已完成！\n下载目录：{output_dir}\n\n是否打开下载目录？",
                QMessageBox.Open | QMessageBox.No,
                QMessageBox.Open,
            )
            if reply == QMessageBox.Open:
                open_path_in_explorer(output_dir)
        else:
            failed_rows = [r for r in range(self.table.rowCount())
                           if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败"]
            msg = "任务结束（有失败或取消）"
            if failed_rows:
                msg += f"\n失败任务数：{len(failed_rows)}"
            self.statusBar().showMessage(msg.replace("\n", " "))
            self.show_tray_message("下载结束", "有任务失败或被取消")

    def show_tray_message(self, title, message):
        if self.tray_icon:
            try:
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 3000)
            except Exception:
                pass

    def cancel_downloads(self):
        if self.sound_player:
            self.sound_player.play("click")
        if self.worker and self.worker.isRunning():
            self.statusBar().showMessage("正在取消...")
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.update_window_title()

    def toggle_pause_queue(self):
        if self.sound_player:
            self.sound_player.play("click")
        if not self.worker:
            return
        if self.worker.paused:
            self.worker.resume()
            self.pause_btn.setText("暂停一下")
            self.statusBar().showMessage("下载中...")
        else:
            self.worker.pause()
            self.pause_btn.setText("继续下载")
            self.statusBar().showMessage("队列已暂停")
        self.update_window_title()

    def on_queue_paused_changed(self, paused):
        if paused:
            self.pause_btn.setText("继续下载")
        else:
            self.pause_btn.setText("暂停一下")
        self.update_window_title()

    def append_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_edit.appendPlainText(line)
        write_runtime_log(msg)

    def log_edit_clear(self):
        self.log_edit.clear()

    # ---------- 任务表右键 ----------

    def show_task_context_menu(self, pos):
        row = self.table.indexAt(pos).row()
        if row < 0:
            return
        menu = QMenu(self.table)
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_copy_err = menu.addAction("复制错误信息")
        act_copy_cell = menu.addAction("复制单元格")
        act_copy_output = menu.addAction("复制输出路径")
        menu.addSeparator()
        act_retry = menu.addAction("重试")
        act_remove = menu.addAction("从队列删除（仅等待中）")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == act_open:
            self.open_task_file(row)
        elif action == act_dir:
            self.open_task_dir(row)
        elif action == act_copy_err:
            self.copy_task_error(row)
        elif action == act_copy_cell:
            self.copy_cell(row, pos)
        elif action == act_copy_output:
            self.copy_task_output(row)
        elif action == act_retry:
            self.retry_task(row)
        elif action == act_remove:
            self.remove_waiting_task(row)

    def on_task_double_clicked(self, index):
        self.open_task_file(index.row())

    def task_output_path(self, row):
        if row < 0 or row >= self.table.rowCount():
            return ""
        item = self.table.item(row, 3)
        return item.text().strip() if item else ""

    def open_task_file(self, row):
        path = self.task_output_path(row)
        if not path:
            QMessageBox.information(self, "提示", "该任务没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def open_task_dir(self, row):
        path = self.task_output_path(row)
        if not path:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def copy_task_error(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        item = self.table.item(row, 2)
        text = item.text() if item else ""
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("已复制错误信息", 3000)

    def copy_cell(self, row, pos):
        item = self.table.itemAt(pos)
        if item:
            QApplication.clipboard().setText(item.text())
            self.statusBar().showMessage("已复制单元格内容", 3000)

    def copy_task_output(self, row):
        path = self.task_output_path(row)
        if path:
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("已复制输出路径", 3000)

    def retry_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        url_item = self.table.item(row, 0)
        if not url_item:
            return
        url = url_item.text().strip()
        if not url:
            return
        self.input_edit.setPlainText(url)
        self.switch_page(0)
        self.start_downloads()

    def remove_waiting_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        status_item = self.table.item(row, 1)
        status_text = status_item.text() if status_item else ""
        if status_text not in {"等待", "已暂停"}:
            QMessageBox.information(self, "提示", "只能删除等待中或已暂停的任务。")
            return
        if self.worker:
            self.worker.skip_index(row)
        self.set_cell(row, 1, "已删除")
        self.set_cell(row, 2, "-")
        self.statusBar().showMessage("已从队列删除", 3000)

    # ---------- 历史 ----------

    def refresh_history(self):
        self.history_records = load_history()
        self.history_table.setRowCount(0)
        for r in self.history_records:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(r.get("title") or "-"))
            status = r.get("status") or "-"
            if status == "completed":
                status = "完成"
            elif status == "failed":
                status = "失败"
            elif status == "cancelled":
                status = "已取消"
            self.history_table.setItem(row, 1, QTableWidgetItem(status))
            self.history_table.setItem(row, 2, QTableWidgetItem(format_bytes(r.get("file_size"))))
            self.history_table.setItem(row, 3, QTableWidgetItem(str(r.get("duration") or "-")))
            self.history_table.setItem(row, 4, QTableWidgetItem(r.get("resolution") or "-"))
            ts = r.get("finished_at")
            time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"
            self.history_table.setItem(row, 5, QTableWidgetItem(time_str))
            path = r.get("output_path") or ""
            if path and not Path(path).exists():
                path = f"{path}  (文件已移动或删除)"
            self.history_table.setItem(row, 6, QTableWidgetItem(path))
        self.statusBar().showMessage(f"历史记录: {len(self.history_records)} 条")

    def history_status_text(self, status):
        mapping = {"completed": "完成", "failed": "失败", "cancelled": "已取消"}
        return mapping.get(status, status or "-")

    def current_history_record(self):
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self.history_records):
            return None, -1
        return self.history_records[row], row

    def copy_history_link(self, row=None):
        if row is None:
            row = self.history_table.currentRow()
        if row < 0 or row >= len(self.history_records):
            return
        url = self.history_records[row].get("url") or ""
        if url:
            QApplication.clipboard().setText(url)
            self.statusBar().showMessage("已复制链接", 3000)

    def redownload_history(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        url = record.get("url") or ""
        if not url:
            QMessageBox.warning(self, "提示", "该历史记录没有可用的链接。")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        self.input_edit.setPlainText(url)
        self.switch_page(0)
        self.start_downloads()

    def open_history_file(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        path = record.get("output_path") or ""
        if not path:
            QMessageBox.information(self, "提示", "该记录没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def open_history_dir(self):
        record, _ = self.current_history_record()
        if not record:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        else:
            path = record.get("output_path") or self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def delete_history(self):
        _, row = self.current_history_record()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        ret = QMessageBox.question(
            self, "删除记录",
            "确定删除这条历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            remove_history_record(row)
            self.refresh_history()

    def clear_all_history(self):
        ret = QMessageBox.question(
            self, "清空历史",
            "确定清空全部历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            save_history([])
            self.refresh_history()

    def export_history(self, fmt):
        """导出历史记录到 CSV 或 JSON 文件。"""
        records = self.history_records or load_history()
        if not records:
            QMessageBox.information(self, "导出", "当前没有历史记录可导出。")
            return
        if fmt == "csv":
            default_name = "bilibili_history.csv"
            filter_str = "CSV 文件 (*.csv);;所有文件 (*)"
        else:
            default_name = "bilibili_history.json"
            filter_str = "JSON 文件 (*.json);;所有文件 (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史记录", default_name, filter_str
        )
        if not path:
            return
        try:
            if fmt == "csv":
                fields = ["title", "url", "status", "file_size", "duration",
                          "resolution", "output_path", "error", "created_at", "finished_at"]
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for r in records:
                        writer.writerow(r)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"已导出 {len(records)} 条记录到 {path}", 5000)
            self.append_log(f"导出历史记录 ({fmt}): {path} ({len(records)} 条)")
            QMessageBox.information(self, "导出成功", f"已导出 {len(records)} 条记录到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def export_settings(self):
        """导出当前设置到 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出设置", "bilibili_settings.json", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"设置已导出到 {path}", 5000)
            self.append_log(f"导出设置: {path}")
            QMessageBox.information(self, "导出成功", f"设置已导出到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def import_settings(self):
        """从 JSON 文件导入设置。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入设置", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                QMessageBox.warning(self, "导入失败", "文件内容不是有效的设置 JSON。")
                return
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            ok, err = save_settings(merged)
            if not ok:
                QMessageBox.warning(self, "导入失败", f"保存设置失败：\n{err}")
                return
            self.settings = merged
            self.apply_settings_to_ui()
            self.statusBar().showMessage(f"设置已从 {path} 导入", 5000)
            self.append_log(f"导入设置: {path}")
            QMessageBox.information(self, "导入成功", "设置已导入并应用。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"导入失败：\n{format_error(exc)}")

    def show_history_context_menu(self, pos):
        index = self.history_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        menu = QMenu(self.history_table)
        act_redownload = menu.addAction("重新下载")
        act_copy_link = menu.addAction("复制链接")
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_delete = menu.addAction("删除记录")
        action = menu.exec_(self.history_table.viewport().mapToGlobal(pos))
        if action == act_redownload:
            self.history_table.selectRow(row)
            self.redownload_history()
        elif action == act_copy_link:
            self.history_table.selectRow(row)
            self.copy_history_link(row)
        elif action == act_open:
            self.history_table.selectRow(row)
            self.open_history_file()
        elif action == act_dir:
            self.history_table.selectRow(row)
            self.open_history_dir()
        elif action == act_delete:
            self.history_table.selectRow(row)
            self.delete_history()

    def on_history_double_clicked(self, index):
        self.history_table.selectRow(index.row())
        self.open_history_file()

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        # 如果托盘可用且不是强制退出，最小化到托盘
        if self.tray_icon and not self._force_quit:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Bilibili 视频下载器",
                "已最小化到托盘，单击图标恢复窗口",
                QSystemTrayIcon.Information, 3000,
            )
            return
        if self.worker and self.worker.isRunning():
            ret = QMessageBox.question(
                self, "确认退出",
                "有下载任务正在进行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.wait(2000)
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            self.cookie_check_worker.wait(2000)
        ok, err = save_settings(self.collect_settings())
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        if self.tray_icon:
            self.tray_icon.hide()
        event.accept()


# ==================== 入口 ====================

def main():
    install_excepthooks()
    DEFAULT_DOWNLOAD_DIR.mkdir(exist_ok=True)

    # 单实例锁：防止重复打开多个窗口抢占资源（下载目录/设置/history 是进程共享的）
    lock_path = BASE_DIR / "app.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None, "Bilibili 视频下载器",
            "程序已在运行中。\n如果确实打不开窗口，请在系统托盘找到图标单击恢复。",
        )
        return

    app = QApplication(sys.argv)
    app.setApplicationName("Bilibili 视频下载器")
    window = MainWindow()
    window.show()
    code = app.exec_()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
