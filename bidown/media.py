"""用 ffmpeg 探测媒体文件的时长、分辨率、编码等信息。"""

import re
import subprocess
from pathlib import Path

from .config import ffmpeg_path, has_ffmpeg


def parse_media_info_text(text):
    """解析 ffmpeg -i stderr 文本。

    旧实现用一条长正则匹配「分辨率 + tbr」，实际 ffmpeg 输出里分辨率与 fps 之间
    还有 SAR/DAR、kb/s 等字段，导致分辨率、帧率、编码几乎永远解析不到。
    这里改为按行提取关键字段，不依赖字段之间的相对顺序。
    """
    info = {}
    if not text:
        return info
    m = re.search(r"Duration:\s*([\d:.]+)", text)
    if m:
        info["duration"] = m.group(1)
    m = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
    if m:
        info["bitrate"] = int(m.group(1))

    video_line = ""
    audio_line = ""
    for line in text.splitlines():
        if not video_line and "Video:" in line:
            video_line = line
        if not audio_line and "Audio:" in line:
            audio_line = line

    if video_line:
        m = re.search(r"Video:\s*([A-Za-z0-9_]+)", video_line)
        if m:
            info["video_codec"] = m.group(1)
        # 分辨率：要求 3~5 位数字，避免误匹配 (avc1 / 0x31637661) 里的十六进制
        m = re.search(r"(\d{3,5})x(\d{3,5})", video_line)
        if m:
            info["width"] = int(m.group(1))
            info["height"] = int(m.group(2))
        m = re.search(r"([\d.]+)\s*fps", video_line)
        if m:
            info["fps"] = float(m.group(1))

    if audio_line:
        m = re.search(r"Audio:\s*([A-Za-z0-9_]+)", audio_line)
        if m:
            info["audio_codec"] = m.group(1)
        m = re.search(r"(\d+)\s*Hz", audio_line)
        if m:
            info["audio_sample_rate"] = int(m.group(1))
    return info


def has_no_audio(info):
    """媒体信息里「有视频流但没有音轨」——下载结果会是无声视频。"""
    return bool(info.get("video_codec")) and not info.get("audio_codec")


def media_info_for_file(path):
    """用 ffmpeg -i 解析媒体信息。"""
    if not has_ffmpeg() or not Path(path).exists():
        return {}
    try:
        proc = subprocess.run(
            [str(ffmpeg_path()), "-hide_banner", "-i", str(path)],
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
