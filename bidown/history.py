"""download/history.json 的读写。"""

import json

from .config import HISTORY_PATH


def load_history():
    try:
        if HISTORY_PATH.exists():
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_history(records):
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def append_history_record(record):
    records = load_history()
    records.insert(0, record)
    records = records[:500]
    save_history(records)


def remove_history_record(index):
    records = load_history()
    if 0 <= index < len(records):
        records.pop(index)
        save_history(records)


def update_history_record(index, updates):
    records = load_history()
    if 0 <= index < len(records):
        records[index].update(updates)
        save_history(records)
