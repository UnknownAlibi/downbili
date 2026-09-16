"""错误信息格式化与 B站错误分类。"""

from .config import BILI_ERROR_CATEGORIES


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
