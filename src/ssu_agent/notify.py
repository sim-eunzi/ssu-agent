"""텔레그램 발신 전용.

양방향(버튼 체크)은 폐기안이다 — 같은 봇 토큰으로 두 프로세스가 폴링하면
업데이트 경합이 난다(핸드오프 §4). 쓰기 경로는 LMS 하나로 유지한다.

메시지에는 항상 html_url 을 붙인다. 탭 한 번에 해당 강의로 들어가는 것이
이 시스템의 목적이다 — 확인하러 가는 행위 자체를 줄이는 것.
"""

import html
import json
import urllib.parse

from . import net
from .config import get
from .risk import fmt_hours, parse_dt

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 4000       # 텔레그램 4096 자 제한에 여유


def esc(s):
    return html.escape(str(s or ""), quote=False)


def link(text, url):
    return '<a href="{}">{}</a>'.format(esc(url), esc(text)) if url else esc(text)


def send(text, cfg=None, dry_run=False, silent=False):
    cfg = cfg or get()
    if dry_run:
        print(text)
        return {"dry_run": True}
    missing = cfg.missing("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError("텔레그램 설정 없음: " + ", ".join(missing))
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": text[:LIMIT],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "disable_notification": "true" if silent else "false",
    }
    _, _, raw = net.post_form(API.format(token=cfg.telegram_token), payload)
    return json.loads(raw.decode("utf-8", "replace"))


# ------------------------------------------------------------- 포맷터
def _dl(iso, now):
    dt = parse_dt(iso)
    if not dt:
        return "마감 미정"
    d = (dt - now).total_seconds() / 86400.0
    if d < 0:
        return "지남"
    tag = "오늘" if d < 1 and dt.date() == now.date() else "D-{}".format(int(d) + 1)
    return "{} {}".format(dt.strftime("%m/%d %H:%M"), tag)


def course_line(c, now):
    left = "{}개 · {}{}".format(c["pending_count"],
                               "≈" if c.get("estimated") else "",
                               fmt_hours(c["remaining_hours"]))
    return "{} <b>{}</b> — {} · {}".format(
        c["icon"], esc(c["stem"]), left, _dl(c["nearest_deadline"], now))


def verdict(total, now):
    """'언제까지 얼마가 필요한데 얼마밖에 없다'를 한 줄로."""
    cr = total["crunch"]
    if not cr["deadline"]:
        return "마감 걸린 잔여 없음."
    dt = parse_dt(cr["deadline"])
    return "{} <b>{}까지</b> {} 필요 / {} 확보 가능".format(
        total["icon"], dt.strftime("%m/%d %H:%M"),
        fmt_hours(cr["need_hours"]), fmt_hours(cr["available_hours"]))


def estimate_note(total):
    """추정이 섞였으면 숨기지 않고 밝힌다."""
    n = total.get("unopened_count") or 0
    if not n:
        return None
    return "<i>≈ 는 추정. 한 번도 안 연 강의 {}개는 길이를 알 수 없어 " \
           "과목 중앙값으로 셌다.</i>".format(n)


def collision_note(assessment):
    """과목별로는 되는데 합치면 안 되는 상황을 짚어준다."""
    t = assessment["total"]
    if t["ratio"] >= 0.8 and all(c["ratio"] < 0.8 for c in assessment["courses"]):
        return "과목별로는 되는데 <b>합치면 안 된다</b>. 겹친 마감이 문제다."
    return None


def morning(assessment, snapshot=None):
    now = parse_dt(assessment["now"])
    t = assessment["total"]
    lines = ["☀️ <b>오늘의 진도</b>  {}".format(now.strftime("%m/%d (%a)")),
             ""]
    hot = [c for c in assessment["courses"] if c["pending_count"]]
    if not hot:
        lines.append("남은 강의 없음. 오늘은 자유다.")
    else:
        for c in hot[:8]:
            lines.append(course_line(c, now))
        lines += ["", "합계 {} 남음".format(fmt_hours(t["remaining_hours"])),
                  verdict(t, now)]
        for note in (collision_note(assessment), estimate_note(t)):
            if note:
                lines.append(note)
    if assessment["overdue"]:
        lines += ["", "⚠️ 마감 지남 {}건".format(len(assessment["overdue"]))]
        for r in assessment["overdue"][:3]:
            lines.append("  · {} {}".format(esc(r["stem"]),
                                            link(r["title"], r["html_url"])))
    return "\n".join(lines)


def evening(assessment, prev_assessment=None):
    now = parse_dt(assessment["now"])
    lines = ["🌙 <b>저녁 점검</b>  {}".format(now.strftime("%m/%d (%a)")), ""]

    if prev_assessment:
        before = prev_assessment["total"]["remaining_hours"]
        after = assessment["total"]["remaining_hours"]
        diff = before - after
        lines.append("오늘 줄인 분량 {}".format(
            fmt_hours(diff) if diff > 0.01 else "없음"))
        lines.append("")

    tomorrow = []
    for c in assessment["courses"]:
        for p in c["pending"][:5]:
            dt = parse_dt(p["deadline"])
            if dt and (dt - now).total_seconds() <= 86400 * 2:
                tomorrow.append((dt, c["stem"], p))
    tomorrow.sort(key=lambda x: x[0])
    if tomorrow:
        lines.append("<b>48시간 안 마감</b>")
        for dt, stem, p in tomorrow[:8]:
            lines.append("  {} · {} {}".format(
                dt.strftime("%m/%d %H:%M"), esc(stem),
                link(p["title"], p["html_url"])))
    else:
        lines.append("48시간 안 마감 없음.")

    urgent = [c for c in assessment["courses"] if c["ratio"] >= 0.8]
    if urgent:
        lines += ["", "<b>내일까지 못 끝낼 위험</b>"]
        for c in urgent[:5]:
            lines.append("  " + course_line(c, now))
    return "\n".join(lines)


def weekly(assessment):
    now = parse_dt(assessment["now"])
    t = assessment["total"]
    lines = ["📅 <b>주간 잔여량</b>  {} 기준".format(now.strftime("%m/%d")), ""]
    for c in assessment["courses"]:
        if not c["pending_count"]:
            continue
        lines.append(course_line(c, now))
    if len(lines) == 2:
        lines.append("남은 강의 없음.")
    lines += ["", "총 {} 남음".format(fmt_hours(t["remaining_hours"])),
              verdict(t, now),
              "{} {} (필요/가용 = {})".format(t["icon"], t["level"], t["ratio"])]
    for note in (collision_note(assessment), estimate_note(t)):
        if note:
            lines.append(note)
    return "\n".join(lines)


def event_message(e):
    if e["type"] == "deadline_moved":
        return "⏰ <b>마감이 앞당겨졌다</b>\n{} · {}\n{} → <b>{}</b>".format(
            esc(e["stem"]), link(e["title"], e.get("html_url")),
            parse_dt(e["from"]).strftime("%m/%d %H:%M"),
            parse_dt(e["to"]).strftime("%m/%d %H:%M"))
    if e["type"] == "new_soon":
        return "🆕 <b>새 항목 · 마감 임박</b>\n{} · {}\n마감 {}".format(
            esc(e["stem"]), link(e["title"], e.get("html_url")),
            parse_dt(e["deadline"]).strftime("%m/%d %H:%M"))
    if e["type"] == "notice":
        return "📢 <b>{}</b>\n{}\n{}".format(
            esc(e["stem"]), link(e["title"], e.get("html_url")),
            esc(e.get("text", ""))[:300])
    return esc(json.dumps(e, ensure_ascii=False))
