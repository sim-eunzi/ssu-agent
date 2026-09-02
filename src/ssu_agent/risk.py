"""잔여량 ÷ 가용시간.

사전녹화 과목의 진짜 리스크는 마감일이 아니라 '남은 분량'이다.
"내일 마감"은 이미 늦고, "일요일까지 6개 = 4.5시간"을 미리 알아야 한다.

가용시간은 config/courses.json 의 availability 를 쓰되, 직장인이라
공부가 가능한 시간대가 정해져 있다고 본다 — 평일 저녁, 주말 낮.
"""

import re
import statistics
from datetime import datetime, time as dtime, timedelta

from .config import KST, get

# duration 이 붙는 콘텐츠. pdf/file 은 '읽을 것'이라 시간 계산에서 뺀다.
VIDEO_TYPES = ("movie", "everlec")

# 안 열어본 항목의 길이를 모를 때 쓰는 값. 실측 중앙값이 25분 근처다.
DEFAULT_DURATION = 1500.0

# 안 열어본 항목의 제목으로 영상/자료를 가른다. 완벽하진 않지만
# 자료를 25분짜리 영상으로 세는 것보다는 낫다.
MATERIAL_RE = re.compile(r"학습자료|강의자료|교안|교재|자료집|참고자료|PPT|PDF|한글파일|hwp",
                         re.IGNORECASE)

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
MIN_SAMPLE = 3


def estimate_duration(items, min_sample=MIN_SAMPLE):
    """실측 영상 길이의 중앙값. 표본이 모자라면 None.

    표본 1~2 개로 60 개를 추정하면 그 한 개가 전체를 흔든다.
    모자라면 호출부가 전체 과목 중앙값으로 넘어가게 한다.
    """
    known = [i["duration"] for i in items
             if i.get("content_type") in VIDEO_TYPES and i.get("duration")]
    return statistics.median(known) if len(known) >= min_sample else None


def remaining_seconds(item, estimate=None):
    """아직 봐야 하는 영상 초. 완료·자료는 0.

    progress 의 스케일(0~1 인지 0~100 인지)이 실측 미확인이라 last_at 을
    바닥값으로 함께 쓴다. 애매하면 '더 남은 쪽'으로 계산한다 — 알림 도구는
    과소평가보다 과대평가가 낫다.

    한 번도 안 연 항목(unopened)은 길이를 알 수 없다. LearningX 의
    attendance_items 는 '내가 연 항목'만 있는 개인 기록이라 404 가 난다.
    100% 남은 것은 확실하므로 중앙값으로 추정한다.
    """
    if item.get("completed"):
        return 0.0
    if item.get("unopened"):
        if MATERIAL_RE.search(item.get("title") or ""):
            return 0.0
        return float(estimate or DEFAULT_DURATION)
    dur = item.get("duration") or 0
    if not dur or item.get("content_type") not in VIDEO_TYPES:
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
def crunch(pending, now, cfg=None):
    """마감 순으로 쌓아 '어느 지점에서 터지는가'를 찾는다.

    단일 자원(내 시간)에 마감이 여럿인 문제다. 각 마감 D 마다
    'D 까지 끝내야 하는 누적 분량' 대 'D 까지 확보 가능한 시간'을 보고,
    그 비율이 가장 나쁜 지점을 고른다. 하나라도 1.0 을 넘으면 불가능하다.

    가장 가까운 마감만 보면 '이번 주는 여유'라고 말해놓고 다음 주에
    한꺼번에 터지는 일이 생긴다. 그걸 막으려는 것.
    """
    cfg = cfg or get()
    by = {}
    for r in pending:
        if r["deadline"] and r["remaining_sec"] > 0:
            by[r["deadline"]] = by.get(r["deadline"], 0.0) + r["remaining_sec"]

    worst = {"ratio": 0.0, "deadline": None, "need_hours": 0.0,
             "available_hours": 0.0}
    cum = 0.0
    for dl in sorted(by):
        cum += by[dl]
        need = cum / 3600.0
        avail = available_hours(now, parse_dt(dl), cfg)
        ratio = (need / avail) if avail > 0 else 99.0
        if ratio > worst["ratio"]:
            worst = {"ratio": round(ratio, 2), "deadline": dl,
                     "need_hours": round(need, 2),
                     "available_hours": round(avail, 2)}
    return worst


def assess(snapshot, now=None, cfg=None):
    """스냅샷 → 과목별/전체 리스크.

    과목 아이콘은 '이 과목만 볼 때', 전체 아이콘은 '다 합쳤을 때'다.
    과목별로는 다 초록인데 합계가 빨간 상황이 실제로 자주 나온다.
    """
    cfg = cfg or get()
    now = now or datetime.now(KST)
    courses, overdue, all_pending = [], [], []

    all_items = [it for c in (snapshot.get("courses") or {}).values()
                 for it in c.get("items", [])]
    global_est = estimate_duration(all_items, min_sample=1)

    for cid, c in (snapshot.get("courses") or {}).items():
        weeks = c.get("weeks") or {}
        est = estimate_duration(c.get("items", [])) or global_est
        pending = []
        for it in c.get("items", []):
            if it.get("kind") != "lecture" or it.get("completed"):
                continue
            unlock = parse_dt(it.get("unlock_at"))
            if unlock and unlock > now:
                continue                    # 아직 안 열림
            dl = deadline_of(it, weeks)
            rec = {
                "item_id": it.get("item_id"),
                "title": it.get("title") or it.get("lx_title"),
                "week": it.get("week"),
                "deadline": dl.isoformat() if dl else None,
                "remaining_sec": remaining_seconds(it, est),
                "content_type": it.get("content_type"),
                "unopened": bool(it.get("unopened")),
                "html_url": it.get("html_url"),
            }
            if dl and dl < now:
                overdue.append(dict(rec, stem=c.get("stem")))
            else:
                pending.append(rec)

        pending.sort(key=lambda r: r["deadline"] or "9999")
        all_pending.extend(pending)
        w = crunch(pending, now, cfg)
        name, icon = level(w["ratio"])
        courses.append({
            "canvas_id": c.get("canvas_id"),
            "stem": c.get("stem"),
            "pending": pending,
            "pending_count": len(pending),
            "remaining_hours": round(
                sum(r["remaining_sec"] for r in pending) / 3600.0, 2),
            "unopened_count": sum(1 for r in pending if r["unopened"]),
            "estimated": any(r["unopened"] and r["remaining_sec"] for r in pending),
            "estimate_sec": round(est) if est else None,
            "nearest_deadline": pending[0]["deadline"] if pending else None,
            "crunch": w,
            "ratio": w["ratio"],
            "level": name,
            "icon": icon,
        })

    courses.sort(key=lambda c: (-c["ratio"], c["nearest_deadline"] or "9999"))

    tw = crunch(all_pending, now, cfg)
    t_name, t_icon = level(tw["ratio"])
    return {
        "now": now.isoformat(timespec="seconds"),
        "courses": courses,
        "overdue": overdue,
        "total": {
            "remaining_hours": round(
                sum(c["remaining_hours"] for c in courses), 2),
            "unopened_count": sum(c["unopened_count"] for c in courses),
            "estimated": any(c["estimated"] for c in courses),
            "crunch": tw,
            "need_hours": tw["need_hours"],
            "available_hours": tw["available_hours"],
            "deadline": tw["deadline"],
            "ratio": tw["ratio"],
            "level": t_name,
            "icon": t_icon,
        },
    }


def fmt_hours(h):
    if h <= 0:
        return "0분"
    m = int(round(h * 60))
    return "{}시간 {}분".format(m // 60, m % 60) if m >= 60 else "{}분".format(m)
