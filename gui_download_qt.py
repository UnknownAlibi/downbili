"""Bilibili 视频下载器 - 启动入口。

实现已按职责拆分到 `bidown/` 包：

    bidown/config.py     路径、常量、默认设置
    bidown/utils.py      纯函数工具
    bidown/urls.py       链接 / BV 号解析
    bidown/media.py      ffmpeg 媒体信息探测
    bidown/history.py    下载历史
    bidown/bilibili.py   B站 Web API
    bidown/workers/      预览、下载、Cookie 检测、扫码登录、封面加载线程
    bidown/ui/           主窗口、扫码对话框、特效控件

运行方式不变：`py gui_download_qt.py`
"""

import sys

from PyQt5.QtCore import QLockFile
from PyQt5.QtWidgets import QApplication, QMessageBox

from bidown.config import BASE_DIR, DEFAULT_DOWNLOAD_DIR
from bidown.logs import install_excepthooks
from bidown.ui.main_window import MainWindow

# 兼容旧的导入路径：单元测试 / 外部脚本仍可从本模块导入这些工具函数
from bidown.errors import format_bili_error, format_error  # noqa: F401
from bidown.media import parse_media_info_text  # noqa: F401
from bidown.urls import (  # noqa: F401
    extract_aid,
    extract_bvid,
    extract_video_id,
    is_bilibili_url,
    normalize_input,
    selected_page_number,
)
from bidown.utils import (  # noqa: F401
    format_bytes,
    format_duration,
    render_filename_template,
    sanitize_filename,
    split_inputs,
)

def main():
    install_excepthooks()
    DEFAULT_DOWNLOAD_DIR.mkdir(exist_ok=True)

    # 必须先创建 QApplication，否则下面的 QMessageBox 会因为“QWidget before QApplication”直接崩溃
    app = QApplication(sys.argv)
    app.setApplicationName("Bilibili 视频下载器")

    # 单实例锁：防止重复打开多个窗口抢占资源（下载目录/设置/history 是进程共享的）
    lock_path = BASE_DIR / "app.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None, "Bilibili 视频下载器",
            "程序已在运行中。\n如果确实打不开窗口，请在系统托盘找到图标单击恢复。",
        )
        return

    window = MainWindow()
    window.show()
    code = app.exec_()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
