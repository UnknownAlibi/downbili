"""B站 Web API：视频信息、播放地址、Cookie 会话、弹幕。"""

import requests
from pathlib import Path

from .config import (
    BILIBILI_DM_LIST_API,
    BILIBILI_PLAYURL_API,
    BILIBILI_VIEW_API,
)
from .errors import format_error
from .logs import write_runtime_log
from .net import std_headers
from .urls import extract_video_id, is_bilibili_url, selected_page_number


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
        except ImportError:
            write_runtime_log("未安装 browser_cookie3，浏览器 Cookie 模式不可用（请安装或改用 cookies.txt）。")
        except Exception as exc:
            write_runtime_log(f"读取浏览器 Cookie 失败: {format_error(exc)}")
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
