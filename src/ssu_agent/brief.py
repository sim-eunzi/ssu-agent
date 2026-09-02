"""수집 결과 → 사람이 읽는 텍스트 / 기계가 읽는 JSON.

**전송하지 않는다.** 텔레그램은 헤르메스봇 하나가 담당한다.
ssu-agent 는 계산해서 stdout 으로 내놓기만 한다 —
eunzi-tools `daily_router.py` 가 쓰는 것과 같은 구조다.

마크업을 붙이지 않는 것도 같은 이유다. HTML 이냐 Markdown 이냐는
보내는 쪽이 정할 일이다.
"""

from .risk import fmt_hours, parse_dt
from . import state


# ------------------------------------------------------------------ 조각
def _dl(iso, now):
    dt = parse_dt(iso)
    if not dt:
        return "마감 미정"
    days = (dt - now).total_seconds() / 86400.0
    if days < 0:
        return "지남"
    tag = "오늘" if dt.date() == now.date() else "D-{}".format(int(days) + 1)
    return "{} {}".format(dt.strftime("%m/%d %H:%M"), tag)


def course_line(c, now):
    return "{} {} — {}개 · {}{} · {}".format(
        c["icon"], c["stem"], c["pending_count"],
        "≈" if c.get("estimated") else "",
        fmt_hours(c["remaining_hours"]), _dl(c["nearest_deadline"], now))


def verdict(total):
    """'언제까지 얼마가 필요한데 얼마밖에 없다'를 한 줄로."""
    cr = total["crunch"]
    if not cr["deadline"]:
        return "마감 걸린 잔여 없음."
    return "{} {}까지 {} 필요 / {} 확보 가능".format(
        total["icon"], parse_dt(cr["deadline"]).strftime("%m/%d %H:%M"),
        fmt_hours(cr["need_hours"]), fmt_hours(cr["available_hours"]))


def notes(assessment):
    out = []
    t = assessment["total"]
    if t["ratio"] >= 0.8 and all(c["ratio"] < 0.8 for c in assessment["courses"]):
        out.append("과목별로는 되는데 합치면 안 된다. 겹친 마감이 문제다.")
    n = t.get("unopened_count") or 0
    if n:
        out.append("≈ 는 추정. 한 번도 안 연 강의 {}개는 길이를 알 수 없어 "
                   "과목 중앙값으로 셌다.".format(n))
    return out


# ------------------------------------------------------------------ 렌더
def morning(a, events=()):
    now = parse_dt(a["now"])
    t = a["total"]
    lines = ["오늘의 진도  {}".format(now.strftime("%m/%d (%a)")), ""]
    hot = [c for c in a["courses"] if c["pending_count"]]
    if not hot:
        lines.append("남은 강의 없음.")
    else:
        lines += [course_line(c, now) for c in hot[:8]]
        lines += ["", "합계 {} 남음".format(fmt_hours(t["remaining_hours"])),
                  verdict(t)]
    if a["overdue"]:
        lines += ["", "마감 지남 {}건".format(len(a["overdue"]))]
        lines += ["  · {} {}".format(r["stem"], r["title"])
                  for r in a["overdue"][:3]]
    return "\n".join(lines + _tail(a, events))


def evening(a, events=()):
    now = parse_dt(a["now"])
    lines = ["저녁 점검  {}".format(now.strftime("%m/%d (%a)")), ""]
    soon = []
    for c in a["courses"]:
        for p in c["pending"]:
            dt = parse_dt(p["deadline"])
            if dt and (dt - now).total_seconds() <= 86400 * 2:
                soon.append((dt, c["stem"], p))
    soon.sort(key=lambda x: x[0])
    if soon:
        lines.append("48시간 안 마감")
        lines += ["  {} · {} {}".format(dt.strftime("%m/%d %H:%M"), stem, p["title"])
                  for dt, stem, p in soon[:8]]
    else:
        lines.append("48시간 안 마감 없음.")
    urgent = [c for c in a["courses"] if c["ratio"] >= 0.8]
    if urgent:
        lines += ["", "내일까지 못 끝낼 위험"]
        lines += ["  " + course_line(c, now) for c in urgent[:5]]
    return "\n".join(lines + _tail(a, events))


def weekly(a, events=()):
    now = parse_dt(a["now"])
    t = a["total"]
    lines = ["주간 잔여량  {} 기준".format(now.strftime("%m/%d")), ""]
    hot = [c for c in a["courses"] if c["pending_count"]]
    lines += [course_line(c, now) for c in hot] or ["남은 강의 없음."]
    lines += ["", "총 {} 남음".format(fmt_hours(t["remaining_hours"])),
              verdict(t), "{} {} (필요/가용 = {})".format(t["icon"], t["level"], t["ratio"])]
    return "\n".join(lines + _tail(a, events))


RENDERERS = {"morning": morning, "evening": evening, "weekly": weekly}


def event_line(e):
    if e["type"] == "deadline_moved":
        return "[마감 앞당겨짐] {} · {} — {} → {}".format(
            e["stem"], e["title"],
            parse_dt(e["from"]).strftime("%m/%d %H:%M"),
            parse_dt(e["to"]).strftime("%m/%d %H:%M"))
    if e["type"] == "new_soon":
        return "[새 항목 · 마감 임박] {} · {} — {}".format(
            e["stem"], e["title"], parse_dt(e["deadline"]).strftime("%m/%d %H:%M"))
    if e["type"] == "notice":
        return "[공지] {} · {} — {}".format(e["stem"], e["title"], e.get("text", ""))
    return str(e)


def _tail(a, events):
    out = []
    if events:
        out += ["", "그 사이 바뀐 것"] + ["  " + event_line(e) for e in events]
    ns = notes(a)
    if ns:
        out += [""] + ns
    return out


def render(a, kind="morning", events=()):
    return RENDERERS[kind](a, events)


# ------------------------------------------------------------------ JSON
def payload(a, kind="morning", events=(), snapshot=None):
    """헤르메스봇이 받아갈 형태. text 는 그대로 전송해도 되게 만들어 둔다."""
    return {
        "generated_at": state.now().isoformat(timespec="seconds"),
        "fetched_at": (snapshot or {}).get("fetched_at"),
        "semester": (snapshot or {}).get("semester"),
        "kind": kind,
        "text": render(a, kind, events),
        "total": a["total"],
        "courses": a["courses"],
        "overdue": a["overdue"],
        "events": list(events),
    }
