"""스냅샷 diff → 즉시 알릴 사건.

핸드오프 §6 M1: 즉시 알림은 3종만. 나머지는 정기 브리핑으로 미룬다.
  1) 마감 앞당겨짐
  2) D-3 이내 신규 항목
  3) 휴강/시험 공지

같은 사건을 두 번 알리지 않도록 notified.json 키를 각 사건이 직접 만든다.
"""

import re
from datetime import timedelta

from .risk import parse_dt
from . import state

MIN_SHIFT = timedelta(minutes=5)   # 초 단위 흔들림은 '앞당겨짐'이 아니다

NOTICE_RE = re.compile(r"휴강|보강|시험|공지\s*변경|일정\s*변경|연기|취소|대체")


def _index(snapshot):
    """(cid, kind, id) → item"""
    out = {}
    for cid, c in (snapshot.get("courses") or {}).items():
        for it in c.get("items", []):
            key = (cid, it.get("kind"),
                   it.get("item_id") or it.get("content_id"))
            if key[2] is not None:
                out[key] = it
    return out


def _deadline(it, course):
    dt = parse_dt(it.get("due_at"))
    if dt:
        return dt
    wk = (course.get("weeks") or {}).get(str(it.get("week"))) or {}
    return parse_dt(wk.get("due_at"))


def detect(curr, prev, now=None, soon_days=3):
    now = now or state.now()
    prev_items = _index(prev) if prev else {}
    prev_ann = {}
    for cid, c in (prev.get("courses") or {}).items() if prev else []:
        for a in c.get("announcements", []) or []:
            prev_ann[(cid, a.get("id"))] = a

    events = []
    first_run = not prev_items

    for cid, c in (curr.get("courses") or {}).items():
        stem = c.get("stem")
        for it in c.get("items", []):
            iid = it.get("item_id") or it.get("content_id")
            if iid is None:
                continue
            key = (cid, it.get("kind"), iid)
            dl = _deadline(it, c)
            old = prev_items.get(key)

            # 1) 마감 앞당겨짐
            if old is not None and dl:
                old_dl = _deadline(old, (prev.get("courses") or {}).get(cid, {}))
                if old_dl and dl < old_dl - MIN_SHIFT:
                    events.append({
                        "type": "deadline_moved",
                        "key": "dl:{}:{}:{}".format(cid, iid, dl.isoformat()),
                        "stem": stem, "title": it.get("title"),
                        "from": old_dl.isoformat(), "to": dl.isoformat(),
                        "html_url": it.get("html_url"),
                    })

            # 2) D-N 이내 신규 (첫 실행은 전부 '신규'라 건너뛴다)
            if old is None and not first_run and dl and not it.get("completed"):
                days = (dl - now).total_seconds() / 86400.0
                if 0 <= days <= soon_days:
                    events.append({
                        "type": "new_soon",
                        "key": "new:{}:{}".format(cid, iid),
                        "stem": stem, "title": it.get("title"),
                        "kind": it.get("kind"),
                        "deadline": dl.isoformat(),
                        "html_url": it.get("html_url"),
                    })

        # 3) 휴강/시험 공지
        for a in c.get("announcements", []) or []:
            if (cid, a.get("id")) in prev_ann or first_run:
                continue
            blob = (a.get("title") or "") + " " + (a.get("text") or "")
            if NOTICE_RE.search(blob):
                events.append({
                    "type": "notice",
                    "key": "ann:{}:{}".format(cid, a.get("id")),
                    "stem": stem, "title": a.get("title"),
                    "text": (a.get("text") or "")[:200],
                    "html_url": a.get("html_url"),
                })

    return events



def unseen(events, store=None):
    store = store if store is not None else state.load(state.NOTIFIED)
    return [e for e in events if e["key"] not in store]
