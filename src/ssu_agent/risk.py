"""잔여량 ÷ 가용시간.

사전녹화 과목의 진짜 리스크는 마감일이 아니라 '남은 분량'이다.
"내일 마감"은 이미 늦고, "일요일까지 6개 = 4.5시간"을 미리 알아야 한다.

가용시간은 config/courses.json 의 availability 를 쓰되, 직장인이라
공부가 가능한 시간대가 정해져 있다고 본다 — 평일 저녁, 주말 낮.
"""

from datetime import datetime, time as dtime, timedelta

from .config import KST, get

# (시작, 끝) 공부 가능 시간대. 평일은 퇴근 후, 주말은 낮.
WINDOW_WEEKDAY = (dtime(19, 0), dtime(23, 59, 59))
WINDOW_WEEKEND = (dtime(10, 0), dtime(22, 0))

LEVELS = [(1.00, "불가", "🔴"),
          (0.80, "위험", "🟠"),
          (0.50, "빠듯", "🟡"),
          (0.00, "여유", "🟢")]


# ------------------------------------------------------------------ 시간
def parse_dt(s):
    """Canvas/LearningX 의 ISO8601(Z) → KST aware datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def _window(day):
    return WINDOW_WEEKEND if day.weekday() >= 5 else WINDOW_WEEKDAY


def _hours_for(day, cfg):
    av = cfg.availability
    return av["weekend_hours"] if day.weekday() >= 5 else av["weekday_hours"]


def available_hours(start, end, cfg=None):
    """start~end 사이에 실제로 쓸 수 있는 공부 시간(시간 단위)."""
    cfg = cfg or get()
    if not end or end <= start:
        return 0.0
    total = 0.0
    day = start.date()
    while day <= end.date():
        w0, w1 = _window(day)
        ws = datetime.combine(day, w0, tzinfo=KST)
        we = datetime.combine(day, w1, tzinfo=KST)
        s, e = max(start, ws), min(end, we)
        if e > s:
            total += _hours_for(day, cfg) * ((e - s).total_seconds()
                                             / (we - ws).total_seconds())
        day += timedelta(days=1)
    return total


# ------------------------------------------------------------------ 잔여
def remaining_seconds(item):
    """아직 봐야 하는 영상 초. 완료·비영상은 0.

    progress 의 스케일(0~1 인지 0~100 인지)이 실측 미확인이라 last_at 을
    바닥값으로 함께 쓴다. 애매하면 '더 남은 쪽'으로 계산한다 — 알림 도구는
    과소평가보다 과대평가가 낫다.
    """
    if item.get("completed"):
        return 0.0
    dur = item.get("duration") or 0
    if not dur or item.get("content_type") != "movie":
        return 0.0
    watched = float(item.get("last_at") or 0)
    p = item.get("progress")
    if p:
        watched = max(watched, dur * (p / 100.0 if p > 1 else float(p)))
    return max(0.0, float(dur) - watched)


def deadline_of(item, weeks):
    """아이템 마감. 없으면 그 주차 마감으로 폴백."""
    dt = parse_dt(item.get("due_at"))
    if dt:
        return dt
    wk = weeks.get(str(item.get("week"))) or {}
    return parse_dt(wk.get("due_at"))


def level(ratio):
    for th, name, icon in LEVELS:
        if ratio >= th:
            return name, icon
    return "여유", "🟢"


# ---------------------------------------------------------------- 집계
def assess(snapshot, now=None, cfg=None):
    """스냅샷 → 과목별/전체 리스크.

    반환: {"now":..., "courses":[...], "total":{...}, "overdue":[...]}
    """
    cfg = cfg or get()
    now = now or datetime.now(KST)
    courses, overdue = [], []

    for cid, c in (snapshot.get("courses") or {}).items():
        weeks = c.get("weeks") or {}
        pending, past = [], []
        for it in c.get("items", []):
            if it.get("kind") != "lecture":
                continue
            if it.get("completed"):
                continue
            dl = deadline_of(it, weeks)
            unlock = parse_dt(it.get("unlock_at"))
            if unlock and unlock > now:
                continue                    # 아직 안 열림
            rec = {
                "item_id": it.get("item_id"),
                "title": it.get("title") or it.get("lx_title"),
                "week": it.get("week"),
                "deadline": dl.isoformat() if dl else None,
                "remaining_sec": remaining_seconds(it),
                "content_type": it.get("content_type"),
                "html_url": it.get("html_url"),
            }
            if dl and dl < now:
                past.append(rec)
            else:
                pending.append(rec)

        pending.sort(key=lambda r: r["deadline"] or "9999")
        need_h = sum(r["remaining_sec"] for r in pending) / 3600.0
        nearest = parse_dt(pending[0]["deadline"]) if pending and pending[0]["deadline"] else None
        avail_h = available_hours(now, nearest, cfg) if nearest else 0.0

        # 가장 가까운 마감까지 처리해야 하는 분량만 비율에 넣는다.
        due_soon_h = sum(r["remaining_sec"] for r in pending
                         if r["deadline"] and parse_dt(r["deadline"]) <= (nearest or now)) / 3600.0
        ratio = (due_soon_h / avail_h) if avail_h > 0 else (99.0 if due_soon_h > 0 else 0.0)
        name, icon = level(ratio)

        courses.append({
            "canvas_id": c.get("canvas_id"),
            "stem": c.get("stem"),
            "pending": pending,
            "pending_count": len(pending),
            "remaining_hours": round(need_h, 2),
            "due_soon_hours": round(due_soon_h, 2),
            "available_hours": round(avail_h, 2),
            "nearest_deadline": nearest.isoformat() if nearest else None,
            "ratio": round(ratio, 2),
            "level": name,
            "icon": icon,
        })
        for r in past:
            overdue.append(dict(r, stem=c.get("stem")))

    courses.sort(key=lambda c: (-c["ratio"], c["nearest_deadline"] or "9999"))

    total_h = sum(c["remaining_hours"] for c in courses)
    # 전체는 학기말까지가 아니라 '가장 먼 임박 마감'까지로 본다.
    horizon = max([parse_dt(c["nearest_deadline"]) for c in courses
                   if c["nearest_deadline"]] or [None])
    total_avail = available_hours(now, horizon, cfg) if horizon else 0.0
    t_ratio = (total_h / total_avail) if total_avail > 0 else (99.0 if total_h else 0.0)
    t_name, t_icon = level(t_ratio)

    return {
        "now": now.isoformat(timespec="seconds"),
        "courses": courses,
        "overdue": overdue,
        "total": {
            "remaining_hours": round(total_h, 2),
            "available_hours": round(total_avail, 2),
            "ratio": round(t_ratio, 2),
            "level": t_name,
            "icon": t_icon,
        },
    }


def fmt_hours(h):
    if h <= 0:
        return "0분"
    m = int(round(h * 60))
    return "{}시간 {}분".format(m // 60, m % 60) if m >= 60 else "{}분".format(m)
