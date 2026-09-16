"""Cookie 有效性检测线程。"""

import requests
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from ..config import BILIBILI_NAV_API
from ..errors import format_error
from ..logs import write_crash_log
from ..net import std_headers


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
        except ImportError as exc:
            raise RuntimeError(
                "未安装 browser_cookie3，无法读取浏览器 Cookie。\n"
                "请执行 py -m pip install browser-cookie3 后重试，"
                "或改用 cookies.txt 文件方式。"
            ) from exc
        try:
            func = {"chrome": browser_cookie3.chrome,
                    "edge": browser_cookie3.edge,
                    "firefox": browser_cookie3.firefox}.get(mode)
            if not func:
                return None
            cj = func(domain_name="bilibili.com")
            return {c.name: c.value for c in cj if "bilibili.com" in c.domain}
        except Exception as exc:
            raise RuntimeError(
                f"读取浏览器 Cookie 失败：{format_error(exc)}\n"
                "请先彻底关闭浏览器（Chrome/Edge 会锁定 Cookie 数据库）后重试，"
                "或改用 cookies.txt 文件方式。"
            ) from exc
