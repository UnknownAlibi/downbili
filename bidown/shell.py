"""调用系统默认程序打开文件 / 目录。"""

import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from .errors import format_error


def open_file_default(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        QMessageBox.warning(None, "打开失败", f"无法打开文件：{format_error(exc)}")


def open_path_in_explorer(path):
    try:
        p = Path(path)
        target = str(p.parent if p.is_file() else p)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        QMessageBox.warning(None, "打开失败", f"无法打开目录：{format_error(exc)}")
