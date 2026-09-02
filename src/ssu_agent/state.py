"""state/ 아래 JSON 상태 파일 읽기/쓰기.

SQLite 를 쓰지 않는다 — 핸드오프 §4. 변경 시에만 쓰므로 파일로 충분하다.
쓰기는 tmp → replace 로 원자적으로 한다 (cron 중복 실행 대비).
"""

import json
import os
import tempfile
from datetime import datetime, timedelta

from .config import KST, STATE_DIR


def _path(name):
    return STATE_DIR / name


def load(name, default=None):
    p = _path(name)
    if not p.exists():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default if default is not None else {}


def save(name, obj):
    STATE_DIR.mkdir(exist_ok=True)
    p = _path(name)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def now():
    return datetime.now(KST)


# --- notified.json ------------------------------------------------------
NOTIFIED = "notified.json"
KEEP_DAYS = 60


def already_notified(key, store=None):
    store = store if store is not None else load(NOTIFIED)
    return key in store


def mark_notified(keys, store=None):
    """알림 발송분을 기록하고 오래된 항목을 정리한다."""
    store = store if store is not None else load(NOTIFIED)
    ts = now().isoformat(timespec="seconds")
    for k in keys:
        store[k] = ts
    cutoff = (now() - timedelta(days=KEEP_DAYS)).isoformat()
    store = {k: v for k, v in store.items() if v >= cutoff}
    save(NOTIFIED, store)
    return store


# --- last_sync.json -----------------------------------------------------
LAST_SYNC = "last_sync.json"


def touch_sync(kind):
    d = load(LAST_SYNC)
    d[kind] = now().isoformat(timespec="seconds")
    save(LAST_SYNC, d)
    return d


def last_sync(kind):
    return load(LAST_SYNC).get(kind)
