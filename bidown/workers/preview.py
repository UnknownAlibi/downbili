"""预览解析线程：优先 yt-dlp，B站 412 时回退公开接口。"""

import time

import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal

from ..bilibili import bili_view
from ..errors import format_bili_error
from ..logs import write_crash_log
from ..net import QuietYtdlpLogger, apply_cookie_and_proxy_options, std_headers
from ..urls import extract_video_id, is_bilibili_url, normalize_input


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
            "upload_date": time.strftime("%Y%m%d", time.localtime(data.get("pubdate"))) if data.get("pubdate") else "",
            "pages": pages,
            "formats": [],
            "source": "B站公开接口（兜底）",
            "url": self.url,
            "note": f"yt-dlp 失败({err_msg[:40]})，已用兜底接口。无 Cookie 时通常仅 720P/360P。",
        }
