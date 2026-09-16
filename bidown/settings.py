"""settings.json 的读写。"""

import json

from .config import DEFAULT_SETTINGS, SETTINGS_PATH


def load_settings():
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged, None
    except Exception as exc:
        return dict(DEFAULT_SETTINGS), str(exc)
    return dict(DEFAULT_SETTINGS), None


def save_settings(settings):
    """保存设置，返回 (True, None) 或 (False, error_msg)。"""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True, None
    except Exception as exc:
        return False, str(exc)
