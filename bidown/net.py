"""HTTP 请求头、yt-dlp 静默 logger、Cookie/代理选项注入。"""

from pathlib import Path


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
