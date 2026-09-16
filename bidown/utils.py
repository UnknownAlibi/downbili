"""纯函数工具：输入拆分、文件名、路径、数值格式化。"""

import re
from pathlib import Path


def split_inputs(text):
    """把输入框文本拆成链接列表。"""
    if not text:
        return []
    parts = re.split(r"[\s,，]+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def sanitize_filename(name, max_len=120):
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name or "")
    return name[:max_len].strip() or "video"


# yt-dlp 风格输出模板：%(title).180B / %(id)s / %(ext)s 等
FILENAME_FIELD_RE = re.compile(r"%\(([^)]+)\)(?:\.(\d+)[BbsdD])?([sdif])?")


def render_filename_template(template, fields, default="%(title)s.%(ext)s"):
    """把 yt-dlp 风格的输出模板渲染成真实文件名。

    B站兜底下载不走 yt-dlp，旧实现直接写死 f"{title}.mp4"，
    导致「文件名模板」设置对 B站无效、下载目录里命名风格不统一。
    未知字段按空串处理，避免用户模板写错就报错。
    """
    template = (template or "").strip() or default

    def repl(match):
        key = match.group(1)
        limit = match.group(2)
        value = fields.get(key, "")
        text = "" if value is None else str(value)
        if limit:
            text = text[: int(limit)]
        return text

    name = FILENAME_FIELD_RE.sub(repl, template)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return sanitize_filename(name)


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
