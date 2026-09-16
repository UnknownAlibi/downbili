"""视频链接 / BV 号解析与规范化。"""

import re
from urllib.parse import parse_qs, urlparse


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
