"""扫码登录线程：生成二维码并轮询登录状态。"""

import time

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from ..config import BILIBILI_QRCODE_GENERATE_API, BILIBILI_QRCODE_POLL_API
from ..errors import format_error
from ..logs import write_crash_log
from ..net import std_headers


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
