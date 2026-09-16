"""崩溃日志与运行日志。"""

import sys
import threading
import time
import traceback

from .config import CRASH_LOG_PATH, RUNTIME_LOG_PATH

MAX_LOG_BYTES = 1024 * 1024


def _rotate_if_needed(path, max_bytes=MAX_LOG_BYTES):
    """日志超过上限时轮转为 xxx.log.1，避免长期使用无限增长。"""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            backup = path.with_name(path.name + ".1")
            if backup.exists():
                backup.unlink()
            path.replace(backup)
    except Exception:
        pass


def write_crash_log(exc_type, exc_value, exc_tb, source="main"):
    """将未捕获异常写入 crash.log。"""
    try:
        _rotate_if_needed(CRASH_LOG_PATH)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n==== {ts} | source={source} ====\n")
            f.write(tb_text)
    except Exception:
        pass


def install_excepthooks():
    """安装全局异常钩子，避免静默闪退。"""
    def qt_hook(exc_type, exc_value, exc_tb):
        write_crash_log(exc_type, exc_value, exc_tb, source="excepthook")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = qt_hook

    def thread_hook(args):
        write_crash_log(args.exc_type, args.exc_value, args.exc_traceback,
                        source=f"thread:{args.thread.name}")
        sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


def write_runtime_log(msg):
    """将运行日志追加到 runtime.log（自动创建目录）。"""
    try:
        RUNTIME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(RUNTIME_LOG_PATH)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RUNTIME_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
