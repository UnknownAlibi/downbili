"""封面图片后台加载线程。"""

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from ..errors import format_error
from ..net import std_headers


class ThumbnailWorker(QThread):
    """后台下载封面图，避免在主线程做网络请求导致界面卡死。"""

    loaded = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, url, proxy="", parent=None):
        super().__init__(parent)
        self.url = url
        self.proxy = proxy

    def run(self):
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            resp = requests.get(self.url, headers=std_headers(), timeout=10, proxies=proxies)
            resp.raise_for_status()
            self.loaded.emit(resp.content)
        except Exception as exc:
            try:
                self.failed.emit(format_error(exc))
            except Exception:
                pass
