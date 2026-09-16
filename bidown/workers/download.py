"""下载线程：yt-dlp / B站兜底、进度上报、取消与临时文件清理。"""

import concurrent.futures
import subprocess
import threading
import time
from pathlib import Path

import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal

from ..bilibili import (
    _build_bili_session,
    bili_playurl,
    bili_view,
    fetch_bili_danmaku_for_url,
)
from ..config import QUALITY_QN, ffmpeg_path, has_ffmpeg
from ..errors import format_bili_error, format_error
from ..logs import write_crash_log
from ..media import has_no_audio, media_info_for_file
from ..net import QuietYtdlpLogger, apply_cookie_and_proxy_options, std_headers
from ..urls import (
    extract_video_id,
    is_bilibili_url,
    normalize_input,
    selected_page_number,
)
from ..utils import (
    extract_output_path,
    render_filename_template,
    sanitize_filename,
    unique_path,
)


class DownloadWorker(QThread):
    item_started = pyqtSignal(int, str)
    item_progress = pyqtSignal(int, float, str)
    item_finished = pyqtSignal(int, str, str)
    item_failed = pyqtSignal(int, str)
    item_history = pyqtSignal(dict)
    log = pyqtSignal(str)
    all_done = pyqtSignal(bool)
    paused_changed = pyqtSignal(bool)

    def __init__(self, inputs, settings, parent=None):
        super().__init__(parent)
        self.inputs = inputs
        self.settings = settings
        self.cancelled = False
        self.paused = False
        self.skip_indices = set()
        self.current_titles = {}
        # 每个下载线程各自的当前输出文件名（并发下载时不能共用同一个属性，否则会串号）
        self._local = threading.local()
        # 本次任务产生的临时文件与已完成的最终文件，用于取消后精确清理
        self._temp_files = set()
        self._temp_lock = threading.Lock()
        self._finished_outputs = set()

    def _track_temp(self, path):
        """登记临时文件路径（含 .part/.ytdl/.temp 变体）。"""
        if not path:
            return
        base = str(path)
        with self._temp_lock:
            self._temp_files.add(base)
            for suffix in (".part", ".ytdl", ".temp"):
                self._temp_files.add(base + suffix)

    def cancel(self):
        self.cancelled = True
        self.paused = False

    def pause(self):
        if not self.cancelled and not self.paused:
            self.paused = True
            self.paused_changed.emit(True)

    def resume(self):
        if self.paused:
            self.paused = False
            self.paused_changed.emit(False)

    def skip_index(self, index):
        self.skip_indices.add(index)

    def wait_while_paused(self, index):
        while self.paused and not self.cancelled:
            time.sleep(0.2)
        return not self.cancelled and index not in self.skip_indices

    def run(self):
        ok = True
        try:
            Path(self.settings["download_dir"]).mkdir(parents=True, exist_ok=True)
            workers = max(1, int(self.settings.get("concurrent_downloads", 1)))
            if workers == 1:
                for index, raw in enumerate(self.inputs):
                    status = self._process_one(index, raw)
                    if status == "cancelled":
                        ok = False
                        break
                    if status == "failed":
                        ok = False
            else:
                ok = self._run_concurrent(workers)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker")
            ok = False
            try:
                self.log.emit(f"下载线程异常: {format_error(exc)}")
            except Exception:
                pass

        try:
            if self.cancelled:
                self.cleanup_temp_files()
            self.all_done.emit(ok and not self.cancelled)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.all_done")

    def _process_one(self, index, raw):
        """处理单个下载任务。返回: 'ok' | 'failed' | 'cancelled' | 'skipped'。"""
        if self.cancelled:
            return "cancelled"
        if index in self.skip_indices:
            self.item_failed.emit(index, "已从队列删除")
            return "skipped"
        if not self.wait_while_paused(index):
            if self.cancelled:
                return "cancelled"
            self.item_failed.emit(index, "已从队列删除")
            return "skipped"

        url = normalize_input(raw)
        if not url:
            return "skipped"

        self.current_titles[index] = url
        self.item_started.emit(index, url)
        self.log.emit(f"开始处理: {url}")
        started_at = int(time.time())
        try:
            if self.should_use_bili_legacy(url):
                outputs = self.download_bili_legacy(index, url)
                output_detail = self.output_detail(outputs)
            else:
                output = self.download_with_ytdlp(index, url)
                output_detail = self.output_detail([output])
            # 下载弹幕
            if self.settings.get("download_danmaku") and is_bilibili_url(url):
                self.item_progress.emit(index, 100, "正在下载弹幕...")
                output_base = self._output_base_for_danmaku(output_detail)
                if output_base:
                    if fetch_bili_danmaku_for_url(url, output_base, self.settings):
                        self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    else:
                        self.log.emit("弹幕下载失败或无弹幕")
            self.item_finished.emit(index, output_detail, "完成")
            self.log.emit(f"完成: {output_detail}")
            self.emit_history(index, url, output_detail, "completed", "", started_at)
            return "ok"
        except Exception as exc:
            if self.cancelled:
                self.item_failed.emit(index, "已取消")
                self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                return "cancelled"
            if self.should_try_bili_fallback(url, exc):
                self.log.emit("yt-dlp 未能完成下载，回退到公开视频兜底接口...")
                try:
                    outputs = self.download_bili_legacy(index, url)
                    output_text = self.output_detail(outputs)
                    if self.settings.get("download_danmaku"):
                        self.item_progress.emit(index, 100, "正在下载弹幕...")
                        output_base = self._output_base_for_danmaku(output_text)
                        if output_base and fetch_bili_danmaku_for_url(url, output_base, self.settings):
                            self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    self.item_finished.emit(index, output_text, "完成（公开视频兜底）")
                    self.log.emit(f"兜底完成: {output_text}")
                    self.emit_history(index, url, output_text, "completed", "", started_at)
                    return "ok"
                except Exception as fallback_exc:
                    if self.cancelled:
                        self.item_failed.emit(index, "已取消")
                        self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                        return "cancelled"
                    err_text = format_bili_error(fallback_exc)
                    self.item_failed.emit(index, err_text)
                    self.log.emit(f"兜底失败: {err_text}")
                    self.emit_history(index, url, "", "failed", err_text, started_at)
                    return "failed"
            err_text = format_bili_error(exc)
            self.item_failed.emit(index, err_text)
            self.log.emit(f"失败: {err_text}")
            self.emit_history(index, url, "", "failed", err_text, started_at)
            return "failed"

    def _run_concurrent(self, max_workers):
        """并发下载多个任务。返回 True 表示全部成功。"""
        ok = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_one, index, raw): index
                for index, raw in enumerate(self.inputs)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    status = future.result()
                    if status in ("failed", "cancelled"):
                        ok = False
                        if status == "cancelled":
                            for f in futures:
                                f.cancel()
                except Exception as exc:
                    write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.concurrent")
                    ok = False
                    try:
                        self.log.emit(f"并发下载异常: {format_error(exc)}")
                    except Exception:
                        pass
        return ok

    def emit_history(self, index, url, output_detail, status, error, started_at):
        title = self.current_titles.get(index) or url
        record = {
            "title": title,
            "url": url,
            "output_detail": output_detail,
            "output_path": extract_output_path(output_detail),
            "status": status,
            "error": error,
            "format_id": (self.settings.get("custom_format") or "").strip(),
            "quality": self.settings.get("quality", ""),
            "created_at": started_at,
            "finished_at": int(time.time()),
        }
        output_path = record["output_path"]
        if output_path and Path(output_path).exists():
            info = media_info_for_file(output_path)
            record.update({
                "file_size": info.get("file_size", 0),
                "duration": info.get("duration", ""),
                "resolution": f"{info.get('width', '')}x{info.get('height', '')}" if info.get("width") else "",
                "fps": info.get("fps", ""),
                "video_codec": info.get("video_codec", ""),
                "audio_codec": info.get("audio_codec", ""),
            })
            if has_no_audio(info):
                # 兜底提醒：任何路径下出现无声视频都能被发现
                record["warning"] = "无音轨"
                self.log.emit(
                    "⚠ 该文件没有音轨（所选格式可能是纯视频流）。"
                    "重新选择带「推荐」的「DASH组合」行再下载即可带上声音。"
                )
        self.item_history.emit(record)

    def output_detail(self, outputs):
        return "\n".join(str(o) for o in outputs if o)

    def _output_base_for_danmaku(self, output_detail):
        """从输出详情中提取弹幕文件的基础路径（去掉扩展名）。"""
        path = extract_output_path(output_detail)
        if not path:
            return ""
        p = Path(path)
        return str(p.parent / p.stem)

    def should_use_bili_legacy(self, url):
        """是否直接用公开视频兜底接口。

        无 Cookie 时 yt-dlp 请求 B站 API 经常被 412 拦截，且 quiet 模式下错误被
        吞掉（download() 返回成功但没生成文件），所以无 Cookie 直接走兜底；
        配置了 Cookie 且 ffmpeg 可用时优先走 yt-dlp（DASH 合并、封面、字幕、
        文件名模板等能力只有 yt-dlp 路径才有），失败后再回退兜底接口。
        """
        if not is_bilibili_url(url):
            return False
        has_cookie = (self.settings.get("cookie_mode") or "none") != "none"
        return not (has_cookie and has_ffmpeg())

    def should_try_bili_fallback(self, url, exc):
        """B站任务失败时是否回退到公开视频兜底接口。"""
        if not is_bilibili_url(url) or self.cancelled:
            return False
        # 兜底接口只返回 durl 单文件直链，不支持仅音频
        if (self.settings.get("quality") or "") == "audio":
            return False
        return True

    def build_ytdlp_options(self, index):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": int(self.settings.get("fragment_threads", 4)),
            "outtmpl": str(Path(self.settings["download_dir"]) / self.settings["filename_template"]),
            "logger": QuietYtdlpLogger(),
            "http_headers": std_headers(),
            "progress_hooks": [lambda d: self.on_ytdlp_progress(index, d)],
            "postprocessor_hooks": [self.on_ytdlp_postprocess],
        }
        custom_format = (self.settings.get("custom_format") or "").strip()
        if custom_format:
            opts["format"] = custom_format
        else:
            quality = self.settings.get("quality", "best")
            audio_q = self.settings.get("audio_quality", "auto")
            if quality == "audio":
                if audio_q == "high":
                    opts["format"] = "bestaudio[abr>=192]/bestaudio/best"
                elif audio_q == "medium":
                    opts["format"] = "bestaudio[abr>=128]/bestaudio/best"
                else:
                    opts["format"] = "bestaudio/best"
            elif quality == "best":
                opts["format"] = "bestvideo+bestaudio/best"
            else:
                qn = QUALITY_QN.get(quality, 80)
                opts["format"] = f"bestvideo[height<={qn}]+bestaudio/best[height<={qn}]/best"
            codec = self.settings.get("codec_preference", "auto")
            if codec == "h264":
                opts["format_sort"] = ["vcodec:h264"]
            elif codec == "hevc":
                opts["format_sort"] = ["vcodec:hevc"]
            elif codec == "av1":
                opts["format_sort"] = ["vcodec:av1"]
            # 音频质量偏好（非仅音频模式也应用）
            if quality != "audio" and audio_q != "auto":
                if audio_q == "high":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:192"]
                elif audio_q == "medium":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:128"]
        if self.settings.get("download_thumbnail"):
            opts["writethumbnail"] = True
        if self.settings.get("download_subtitle"):
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = ["zh-Hans", "zh", "en"]
        if has_ffmpeg():
            opts["ffmpeg_location"] = str(ffmpeg_path().parent)
        return apply_cookie_and_proxy_options(opts, self.settings)

    def on_ytdlp_progress(self, index, data):
        if self.cancelled:
            raise RuntimeError("用户取消下载")
        status = data.get("status")
        filename = data.get("filename") or data.get("tmpfilename") or ""
        if filename:
            self._local.filename = filename
            self._track_temp(data.get("filename"))
            self._track_temp(data.get("tmpfilename"))
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = downloaded / total * 100 if total else 0
            speed = data.get("speed") or 0
            eta = data.get("eta")
            detail = f"{percent:.1f}%"
            if speed:
                detail += f"  {speed / 1024 / 1024:.2f} MB/s"
            if eta is not None:
                detail += f"  ETA {eta}s"
            self.item_progress.emit(index, percent, detail)
        elif status == "finished":
            self.item_progress.emit(index, 100, "下载完成，正在合并/整理")
        elif status == "error":
            self.item_progress.emit(index, 0, "下载错误")

    def on_ytdlp_postprocess(self, d):
        """后处理钩子：合并/转码完成后更新最终文件名。
        DASH 流下载时 progress_hook 记录的是临时分片文件，合并后会被删除，
        这里用 postprocessor 的 filepath 覆盖为最终输出文件，确保历史记录能取到真实路径。"""
        try:
            filepath = d.get("filepath") or ""
            if filepath:
                self._local.filename = filepath
        except Exception:
            pass

    def download_with_ytdlp(self, index, url):
        self._local.filename = ""
        with yt_dlp.YoutubeDL(self.build_ytdlp_options(index)) as ydl:
            result = ydl.download([url])
        if result:
            raise RuntimeError(f"yt-dlp 返回错误码: {result}")
        final = getattr(self._local, "filename", "") or ""
        # 验证：如果最终文件不存在（可能是 yt-dlp 静默失败，如 412 风控），抛错而非返回目录
        if not final or not Path(final).is_file():
            raise RuntimeError(
                "yt-dlp 未生成有效文件（可能被 B站 412 风控拦截或格式无效）。\n"
                "建议：清除选定的格式（留空）后重试，或配置 Cookie 后重试。"
            )
        self._finished_outputs.add(str(final))
        return final

    def request_session(self):
        """构建带 Cookie 和代理的下载 session。"""
        session = _build_bili_session(self.settings)
        return session

    def download_bili_legacy(self, index, url):
        id_type, id_value = extract_video_id(url)
        if not id_value:
            raise RuntimeError("无法从链接中识别 BV 号或 av 号。")
        video_id = url  # bili_view/bili_playurl 内部会解析
        page_num = selected_page_number(url)
        data = bili_view(video_id, self.settings)
        pages = data.get("pages") or []
        if not pages:
            raise RuntimeError("没有找到可下载的分 P。")
        cid = None
        for p in pages:
            if (p.get("page") or 1) == page_num:
                cid = p.get("cid")
                break
        if cid is None:
            cid = pages[0].get("cid")
            page_num = pages[0].get("page") or 1
        quality = self.settings.get("quality", "best")
        if quality == "audio":
            raise RuntimeError("B站公开视频兜底接口不支持仅音频。请配置 Cookie 后重试。")
        has_cookie = (self.settings.get("cookie_mode") or "none") != "none"
        # 如果用户在预览表选了具体格式（如 30080 / 30080+30280），从中解析出视频清晰度 qn
        # B站 DASH 流 format_id：视频 300xx，音频 302xx；qn = format_id - 30000
        custom_format = (self.settings.get("custom_format") or "").strip()
        qn_from_format = None
        if custom_format:
            for part in custom_format.split("+"):
                part = part.strip()
                if part.isdigit() and part.startswith("300"):
                    qn_from_format = int(part) - 30000
                    break
        if qn_from_format:
            qn = qn_from_format
            self.log.emit(f"按选定格式解析清晰度 qn={qn}")
        elif not has_cookie:
            qn = 16  # 360P
            self.log.emit("未配置 Cookie，兜底接口尝试 360P 低清晰度...")
        else:
            qn = QUALITY_QN.get(quality, 80) if quality != "best" else 80
        play_data = bili_playurl(video_id, cid, qn, self.settings, page=page_num)
        durl = play_data.get("durl") or []
        if not durl:
            # 如果有 Cookie 且返回了 dash，尝试用 fnval=0 重新请求 durl
            dash = play_data.get("dash")
            if dash:
                self.log.emit("返回了 DASH 格式，尝试请求 durl 直链...")
                play_data = bili_playurl(video_id, cid, qn, self.settings, page=page_num, fnval=0)
                durl = play_data.get("durl") or []
            if not durl:
                raise RuntimeError(
                    "兜底接口没有返回可下载直链。可能原因：\n"
                    "1. 视频需要登录/大会员 → 请配置 Cookie\n"
                    "2. 视频被删除/审核中\n"
                    "3. B站风控 → 请稍后再试或配置 Cookie\n"
                    "建议：设置页 → 扫码登录配置 Cookie 后重试。"
                )
        session = self.request_session()
        download_dir = Path(self.settings["download_dir"])
        title = data.get("title") or id_value
        part_title = ""
        if len(pages) > 1:
            page_part = pages[0]
            for p in pages:
                if (p.get("page") or 1) == page_num:
                    page_part = p
                    break
            part_title = page_part.get("part") or f"P{page_num}"
        # 按用户设置的文件名模板生成，与 yt-dlp 路径保持一致的命名风格
        filename = render_filename_template(
            self.settings.get("filename_template"),
            {
                "title": title,
                "id": id_value,
                "ext": "mp4",
                "uploader": (data.get("owner") or {}).get("name") or "",
                "page": page_num,
                "part": part_title,
            },
        )
        if not Path(filename).suffix:
            filename = f"{filename}.mp4"
        if len(pages) > 1:
            filename = f"{Path(filename).stem}_P{page_num}_{sanitize_filename(part_title)}.mp4"
        output_path = unique_path(download_dir / filename)
        outputs = []
        total_bytes = 0
        for i, d in enumerate(durl):
            video_url = d.get("url")
            if not video_url:
                continue
            if self.cancelled:
                raise RuntimeError("用户取消下载")
            self.item_progress.emit(index, 0, f"下载分段 {i+1}/{len(durl)}")
            resp = session.get(video_url, headers={"Referer": "https://www.bilibili.com/"}, stream=True, timeout=30)
            resp.raise_for_status()
            size = int(d.get("size") or 0)
            downloaded = 0
            part_path = output_path.with_suffix(f".part{i}") if len(durl) > 1 else output_path.with_suffix(".part")
            self._track_temp(str(part_path))
            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if self.cancelled:
                        raise RuntimeError("用户取消下载")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        total_bytes += len(chunk)
                        if size:
                            pct = downloaded / size * 100
                            self.item_progress.emit(index, pct, f"分段 {i+1}/{len(durl)}  {pct:.1f}%")
            if len(durl) > 1:
                outputs.append(str(part_path))
            else:
                part_path.replace(output_path)
                outputs.append(str(output_path))
        if len(durl) > 1 and has_ffmpeg():
            concat_path = output_path.with_suffix(".concat.txt")
            self._track_temp(str(concat_path))
            with open(concat_path, "w", encoding="utf-8") as f:
                for o in outputs:
                    f.write(f"file '{o}'\n")
            try:
                subprocess.run(
                    [str(ffmpeg_path()), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
                     "-c", "copy", str(output_path)],
                    check=True,
                    timeout=300,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for o in outputs:
                    try:
                        Path(o).unlink()
                    except Exception:
                        pass
                try:
                    concat_path.unlink()
                except Exception:
                    pass
                outputs = [str(output_path)]
            except Exception:
                pass
        self._finished_outputs.add(str(output_path))
        self.item_progress.emit(index, 100, "完成")
        return outputs

    def cleanup_temp_files(self):
        """清理本次任务产生的临时文件。

        只删除本 worker 登记过的 .part/.ytdl/.temp/分段文件，不再扫描整个下载目录，
        避免误删用户自己放进下载目录的同名文件；已完成的成品文件永不删除。
        """
        with self._temp_lock:
            targets = list(self._temp_files)
            self._temp_files.clear()
        for target in targets:
            if target in self._finished_outputs:
                continue
            try:
                p = Path(target)
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
