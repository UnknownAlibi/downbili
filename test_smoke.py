"""界面与线程装配的冒烟测试（离屏模式，不弹窗、不联网）。

重构后用它守住底线：主窗口能构建、三个页面都在、各 Worker 能正常实例化、
B站 下载路径选择符合预期。

运行方式:
    pytest test_smoke.py -v
"""

import os
import sys
from pathlib import Path

# 必须在导入 PyQt5 之前设置，保证无显示器环境也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
    QTableWidgetItem,
)

from bidown.config import DEFAULT_SETTINGS, has_ffmpeg  # noqa: E402
from bidown.ui.main_window import MainWindow  # noqa: E402
from bidown.workers.cookie import CookieCheckWorker  # noqa: E402
from bidown.workers.download import DownloadWorker  # noqa: E402
from bidown.workers.preview import PreviewWorker  # noqa: E402
from bidown.workers.thumbnail import ThumbnailWorker  # noqa: E402

BILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_paths_point_to_project_root():
    """BASE_DIR 必须落在项目根目录，不能跑进 bidown/ 包里。

    这是包结构重构最容易踩的坑：__file__ 从根目录变成了 bidown/config.py。
    """
    from bidown import config

    root = Path(__file__).resolve().parent
    assert config.BASE_DIR == root
    assert config.SETTINGS_PATH == root / "settings.json"
    assert config.DEFAULT_DOWNLOAD_DIR == root / "download"
    assert config.HISTORY_PATH == root / "download" / "history.json"
    assert config.CRASH_LOG_PATH == root / "crash.log"
    assert config.RESOURCE_DIR == root
    assert not (root / "bidown" / "settings.json").exists()


def test_main_window_builds(qt_app):
    window = MainWindow()
    try:
        assert window.content_stack.count() == 3
        window.switch_page(1)
        assert window.content_stack.currentIndex() == 1
        window.switch_page(0)
        window.refresh_history()
        window.clear_preview()
        window.update_window_title()
        assert window.formats_table.rowCount() == 0
    finally:
        # 不调用 close()：closeEvent 会回写 settings.json，测试不应改动用户配置
        window.deleteLater()


def show_formats(window, formats=None):
    """模拟预览回调：format 列表与表格一起就绪（真实流程见 on_preview_ready）。"""
    window.preview_formats = [dict(f) for f in (formats or FORMATS_SAMPLE)]
    window.fill_formats_table(window.preview_formats)


def bili_stream_sample():
    """模拟真实 B站 DASH 返回：4 分辨率 × 3 编码 + 3 条音频，没有任何自带声音的流。"""
    formats = []
    for audio_id, tbr in (("30216", 66), ("30232", 96), ("30280", 165)):
        formats.append({"format_id": audio_id, "ext": "m4a", "vcodec": "none",
                        "acodec": "mp4a.40.2", "width": 0, "height": 0, "fps": 0,
                        "filesize": tbr * 1000, "tbr": tbr, "note": ""})
    codecs = ("avc1.640033", "hvc1.1.6.L120.90", "av01.0.08M.08")
    for height, ids in ((360, ("30016", "30011", "100022")),
                        (480, ("30032", "30033", "100023")),
                        (720, ("30064", "30066", "100024")),
                        (1080, ("30080", "30077", "100026"))):
        for format_id, vcodec in zip(ids, codecs):
            formats.append({"format_id": format_id, "ext": "mp4", "vcodec": vcodec,
                            "acodec": "none", "width": int(height * 16 / 9), "height": height,
                            "fps": 30, "filesize": 1000000, "tbr": 1000, "note": ""})
    return formats


def test_grouped_format_view_has_sound_for_every_row(qt_app):
    """默认视图按分辨率合并：每一行都是「视频+音频」，不会再列出无声的纯视频流。"""
    window = MainWindow()
    try:
        window.custom_format_edit.clear()
        show_formats(window, bili_stream_sample())
        table = window.formats_table
        types = [table.item(row, 2).text() for row in range(table.rowCount())]
        assert types.count("视频+音频") == 4, types
        assert types.count("音频") == 1, types
        assert all(t != "视频" for t in types), types
        for row in range(table.rowCount()):
            format_id = table.item(row, 1).text()
            assert format_id == "30280" or format_id.endswith("+30280"), format_id
        assert table.item(0, 3).text() == "1920x1080"
        assert table.item(0, 0).text() == "推荐"

        # 勾选后可以看到并单独选择原始流
        window.show_streams_check.setChecked(True)
        raw_types = [table.item(row, 2).text() for row in range(table.rowCount())]
        assert raw_types.count("视频") == 12, raw_types
        assert raw_types.count("音频") == 3, raw_types
    finally:
        window.deleteLater()


def test_no_clipped_text_or_orphan_widgets(qt_app):
    """界面体检：文字不能被裁、设置项不能脱离布局（曾是"文字显示不全"的根因）。

    回归点：
    1. 侧边栏 210px 时「BiliDown」需要 234px，被裁；
    2. 在已有布局的卡片上再 QGridLayout(card)，布局安装失败，
       下载设置/网络与文件名两张卡片的控件全部没有父控件、不显示。
    """
    window = MainWindow()
    try:
        window.resize(1280, 860)
        window.show()
        problems = []
        for page in range(3):
            window.switch_page(page)
            for _ in range(4):
                qt_app.processEvents()
            for cls in (QLabel, QPushButton, QCheckBox):
                for widget in window.findChildren(cls):
                    if not widget.isVisible():
                        continue
                    if isinstance(widget, QLabel) and widget.pixmap() and not widget.pixmap().isNull():
                        continue
                    text = widget.text()
                    if not text:
                        continue
                    if isinstance(widget, QLabel) and widget.wordWrap():
                        needed = widget.heightForWidth(max(widget.width(), 1))
                        if widget.height() < needed - 2:
                            problems.append(f"高度不足 {text[:16]!r}")
                    else:
                        if widget.width() < widget.sizeHint().width() - 2:
                            problems.append(f"宽度不足 {text[:16]!r}")
                        if widget.height() < widget.sizeHint().height() - 2:
                            problems.append(f"高度不足 {text[:16]!r}")
        assert not problems, "有控件文字被裁切: " + "; ".join(problems[:6])

        # 设置页的控件必须真正挂进布局并可见
        window.switch_page(2)
        for _ in range(3):
            qt_app.processEvents()
        for name, widget in (
            ("quality_combo", window.quality_combo),
            ("codec_combo", window.codec_combo),
            ("audio_quality_combo", window.audio_quality_combo),
            ("concurrent_spin", window.concurrent_spin),
            ("custom_format_edit", window.custom_format_edit),
            ("dir_edit", window.dir_edit),
            ("proxy_edit", window.proxy_edit),
            ("template_edit", window.template_edit),
        ):
            assert widget.parentWidget() is not None, f"{name} 脱离了布局"
            assert widget.isVisible(), f"{name} 不可见"
    finally:
        window.deleteLater()


def test_fill_formats_table_renders_rows(qt_app):
    """预览拿到格式后，表格必须真的填出行。

    回归点：重构时把 FFMPEG_EXE.exists() 批量替换成 has_ffmpeg()，
    撞上函数内原有局部变量 has_ffmpeg，导致整张格式表空白。
    """
    window = MainWindow()
    try:
        formats = [
            {"format_id": "30080", "ext": "mp4", "vcodec": "avc1.640032", "acodec": "none",
             "width": 1920, "height": 1080, "fps": 30, "filesize": 1000000, "tbr": 2000, "note": ""},
            {"format_id": "30280", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "width": 0, "height": 0, "fps": 0, "filesize": 200000, "tbr": 128, "note": ""},
        ]
        show_formats(window, formats)
        # 默认分组视图：1 个「1080P 视频+音频」行 + 1 个「仅音频」行
        assert window.formats_table.rowCount() == 2
        expected_format = "30080+30280" if has_ffmpeg() else "30080"
        assert window.formats_table.item(0, 1).text() == expected_format
        assert window.formats_table.item(0, 3).text() == "1920x1080"
        assert window.formats_table.item(1, 2).text() == "音频"
        window.fill_formats_table([])
        assert window.formats_table.rowCount() == 0
    finally:
        window.deleteLater()


FORMATS_SAMPLE = [
    {"format_id": "30080", "ext": "mp4", "vcodec": "avc1.640032", "acodec": "none",
     "width": 1920, "height": 1080, "fps": 30, "filesize": 1000000, "tbr": 2000, "note": ""},
    {"format_id": "30280", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "width": 0, "height": 0, "fps": 0, "filesize": 200000, "tbr": 128, "note": ""},
]


def test_format_selection_is_visible(qt_app):
    """选中格式后必须在下载页看到：提示条变绿 + 表格出现「✔ 已选」，清除后复原。"""
    window = MainWindow()
    try:
        window.custom_format_edit.clear()
        show_formats(window)
        assert "未选择格式" in window.selected_format_label.text()

        window.formats_table.selectRow(0)
        window.use_selected_format()
        expected = "30080+30280" if has_ffmpeg() else "30080"
        assert window.custom_format_edit.text() == expected
        assert "已选格式：" in window.selected_format_label.text()
        assert "1920x1080" in window.selected_format_label.text()
        marked = window.formats_table.item(0, 0).text()
        assert marked.startswith("✔ 已选")

        window.clear_selected_format()
        assert window.custom_format_edit.text() == ""
        assert "未选择格式" in window.selected_format_label.text()
        assert not window.formats_table.item(0, 0).text().startswith("✔ 已选")

        # 选了一个当前链接没有的格式时给出警告态
        window.custom_format_edit.setText("99999")
        window.refresh_selected_format_label()
        assert "没有匹配项" in window.selected_format_label.text()
    finally:
        window.deleteLater()


def test_format_choice_resolution(qt_app):
    """纯视频流必须自动补音频流，否则下载出来是没有声音的视频。"""
    window = MainWindow()
    try:
        # 原始流视图下才有单独的纯视频流/纯音频流行
        window.custom_format_edit.clear()
        window.show_streams_check.setChecked(True)
        show_formats(window)
        assert window.formats_table.item(0, 0).text() == "视频流"
        assert window.formats_table.item(1, 0).text() == "音频流"

        chosen, hint = window._resolve_format_choice(0, "30080")
        if has_ffmpeg():
            assert chosen == "30080+30280"
            assert "补上音频流" in hint
        else:
            assert chosen == "30080"
            assert "没有声音" in hint

        chosen, hint = window._resolve_format_choice(1, "30280")
        assert chosen == "30280"
        assert "仅音频" in hint

        # 下载前兜底：设置里残留的纯视频流也会被自动补上音频
        if has_ffmpeg():
            assert window._ensure_audio_track("30080") == "30080+30280"
        assert window._ensure_audio_track("30080+30280") == "30080+30280"
        assert window._ensure_audio_track("") == ""

        if has_ffmpeg():
            combo_row = window._format_row_for("30080+30280")
            assert combo_row is not None
            chosen, hint = window._resolve_format_choice(combo_row, "30080+30280")
            assert chosen == "30080+30280"
            assert "需要 ffmpeg 合并" in hint
    finally:
        window.deleteLater()


def test_queue_counts_ignore_deleted(qt_app):
    """「已从队列删除」的任务不应计入进度分母。"""
    window = MainWindow()
    try:
        window.table.setSortingEnabled(False)
        window.table.setRowCount(3)
        for row in range(3):
            window.table.setItem(row, 0, QTableWidgetItem(f"url{row}"))
            window.table.setItem(row, 1, QTableWidgetItem("等待"))
        assert window._queue_counts() == (3, 0)
        window.table.item(1, 1).setText("已删除")
        assert window._queue_counts() == (2, 0)
        window.table.item(0, 1).setText("完成")
        window.table.item(2, 1).setText("失败")
        assert window._queue_counts() == (2, 2)
        window._update_progress_bar()
        assert window.progress_bar.value() == 100
    finally:
        window.deleteLater()


def test_bilibili_path_selection(qt_app):
    settings = dict(DEFAULT_SETTINGS)
    settings["cookie_mode"] = "none"
    worker = DownloadWorker([BILI_URL], settings)
    assert worker.should_use_bili_legacy(BILI_URL) is True, "无 Cookie 时应走兜底接口"
    assert worker.should_use_bili_legacy("https://youtube.com/watch?v=a") is False

    settings["cookie_mode"] = "file"
    worker_with_cookie = DownloadWorker([BILI_URL], settings)
    assert worker_with_cookie.should_use_bili_legacy(BILI_URL) is (not has_ffmpeg())


def test_workers_can_be_constructed(qt_app):
    settings = dict(DEFAULT_SETTINGS)
    preview = PreviewWorker(1, "BV1xx411c7mD", settings)
    assert preview.ytdlp_options()["skip_download"] is True
    assert preview.url == BILI_URL  # BV 号会被补全成完整链接

    assert DownloadWorker([BILI_URL], settings).build_ytdlp_options(0)["outtmpl"]
    assert CookieCheckWorker(settings).load_cookies() == {}
    assert ThumbnailWorker("https://example.com/a.png").url.endswith("a.png")
