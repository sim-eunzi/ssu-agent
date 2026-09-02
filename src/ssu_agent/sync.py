"""LMS 수집 → state/snapshot.json.

Canvas 모듈(주차 구조·퀴즈/과제 마감) + LearningX lessons(주차 마감) +
attendance_items(진도·영상 길이) 를 한 스냅샷으로 합친다.

attendance_items 목록 엔드포인트는 500 이라 N+1 이 불가피하다(핸드오프 §2.2).
이미 completed 인 아이템은 다시 안 뒤집히므로 이전 스냅샷에서 재사용해
요청 수를 줄인다. --full 로 끌 수 있다.
"""

import html as html_mod
import re
from concurrent.futures import ThreadPoolExecutor

from datetime import timedelta

from . import canvas as canvas_mod
from . import net, state
from .canvas import ATTENDANCE_RE
from .config import get

SNAPSHOT = "snapshot.json"
PREV = "snapshot.prev.json"
WEEK_RE = re.compile(r"(\d+)\s*주\s*차")
WORKERS = 4


def week_of(name):
    m = WEEK_RE.search(name or "")
    return int(m.group(1)) if m else None


# ----------------------------------------------------------- Canvas 파트
def scan_modules(cv, cid):
    """모듈 → (출석아이템 목록, 퀴즈/과제 아이템 목록, 주차→모듈명)."""
    lectures, graded, week_names = [], [], {}
    for m in cv.modules(cid):
        wk = week_of(m.get("name"))
        if wk:
            week_names[wk] = m.get("name")
        for it in m.get("items") or []:
            typ = it.get("type")
            common = {
                "week": wk,
                "module": m.get("name"),
                "position": it.get("position"),
                "title": it.get("title"),
                "html_url": it.get("html_url"),
            }
            if typ == "ExternalTool":
                hit = ATTENDANCE_RE.search(it.get("external_url") or "")
                if hit:                      # tool 41(Q&A 게시판)은 여기서 걸러진다
                    d = dict(common)
                    d.update(kind="lecture", item_id=int(hit.group(1)))
                    lectures.append(d)
            elif typ in ("Quiz", "Assignment", "Discussion"):
                cd = it.get("content_details") or {}
                d = dict(common)
                d.update(kind=typ.lower(),
                         content_id=it.get("content_id"),
                         due_at=cd.get("due_at"),
                         unlock_at=cd.get("unlock_at"),
                         lock_at=cd.get("lock_at"),
                         points=cd.get("points_possible"),
                         locked=bool(cd.get("locked_for_user")))
                graded.append(d)
    return lectures, graded, week_names


# -------------------------------------------------------- LearningX 파트
def parse_lessons(raw):
    """lessons 배열 → {주차번호: 마감 메타}.

    lessons[].lessons[] 는 수업 일정 메타일 뿐이라 보지 않는다(핸드오프 §2.2).
    """
    out = {}
    if not isinstance(raw, list):
        return out
    for i, w in enumerate(raw, 1):
        if not isinstance(w, dict):
            continue
        wk = w.get("week_position") or w.get("position") or w.get("week") or i
        try:
            wk = int(wk)
        except (TypeError, ValueError):
            wk = i
        out[wk] = {
            "title": w.get("title") or w.get("name"),
            "due_at": w.get("due_at"),
            "late_at": w.get("late_at"),
            "unlock_at": w.get("unlock_at"),
            "lock_at": w.get("lock_at"),
        }
    return out


def flatten_attendance(raw):
    """attendance_items/{id} 응답에서 필요한 것만 뽑는다."""
    if not isinstance(raw, dict):
        return {}
    ad = raw.get("attendance_data") or {}
    cd = raw.get("item_content_data") or {}
    return {
        "opened": bool(raw.get("opened")),
        "unopened": False,
        "lx_title": raw.get("title"),
        "lx_week": raw.get("week_position"),
        "lx_lesson": raw.get("lesson_position"),
        "due_at": raw.get("due_at"),
        "late_at": raw.get("late_at"),
        "unlock_at": raw.get("unlock_at"),
        "completed": bool(ad.get("completed")),
        "attendance_status": ad.get("attendance_status"),
        "progress": ad.get("progress"),
        "last_at": ad.get("last_at"),
        "content_type": cd.get("content_type"),
        "duration": cd.get("duration"),
        "view_url": cd.get("view_url"),
        "file_name": cd.get("file_name"),
        "total_file_size": cd.get("total_file_size"),
    }


TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s, limit=300):
    if not s:
        return ""
    txt = html_mod.unescape(TAG_RE.sub(" ", s))
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def scan_announcements(cv, cid, since_days=14):
    """휴강·시험 공지를 잡기 위한 최근 공지."""
    start = (state.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    out = []
    for a in cv.announcements(cid, start_date=start) or []:
        out.append({
            "id": a.get("id"),
            "title": a.get("title"),
            "posted_at": a.get("posted_at") or a.get("created_at"),
            "html_url": a.get("html_url"),
            "text": strip_html(a.get("message")),
        })
    return out


# ------------------------------------------------------------------ 본체
def _prev_lectures(prev, cid):
    """이전 스냅샷의 completed 강의 아이템 색인."""
    course = (prev.get("courses") or {}).get(str(cid)) or {}
    return {it["item_id"]: it for it in course.get("items", [])
            if it.get("kind") == "lecture" and it.get("completed")}


def run(full=False, only=None, verbose=False):
    cfg = get()
    cv = canvas_mod.Canvas(cfg)
    lx = canvas_mod.LearningX(cv, cfg)
    prev = state.load(SNAPSHOT)

    course_meta = {}
    for c in cv.courses():
        course_meta[c.get("id")] = c.get("name")

    snap = {"fetched_at": state.now().isoformat(timespec="seconds"),
            "semester": cfg.semester, "courses": {}}

    targets = [c for c in cfg.courses
               if only is None or c["canvas_id"] in only]

    for c in targets:
        cid = c["canvas_id"]
        entry = {"canvas_id": cid, "stem": c["stem"], "mode": c.get("mode"),
                 "name": course_meta.get(cid), "weeks": {}, "items": [],
                 "errors": []}
        snap["courses"][str(cid)] = entry

        try:
            lectures, graded, week_names = scan_modules(cv, cid)
        except net.HttpError as e:
            entry["errors"].append("modules: " + str(e))
            continue

        entry["week_names"] = week_names
        entry["items"].extend(graded)

        try:
            entry["weeks"] = {str(k): v for k, v in
                              parse_lessons(lx.lessons(cid)).items()}
        except (net.HttpError, RuntimeError) as e:
            entry["errors"].append("lessons: " + str(e))

        reuse = {} if full else _prev_lectures(prev, cid)

        def fetch(rec):
            iid = rec["item_id"]
            if iid in reuse:
                merged = dict(reuse[iid])
                merged.update(rec)
                merged["stale"] = True
                return merged
            try:
                data = flatten_attendance(lx.attendance_item(cid, iid))
                if not data:
                    rec["errors"] = "빈 응답"
                merged = dict(rec)
                merged.update(data)
                return merged
            except net.HttpError as e:
                merged = dict(rec)
                if e.code == 404:
                    # attendance_items 는 '내가 연 항목'만 있는 개인 기록이다.
                    # 404 = 한 번도 안 열었다 = 100% 남았다. 에러가 아니라 정보다.
                    # 길이를 알려면 LTI 를 런치해야 하는데, 그건 열람 기록을
                    # 남기므로 하지 않는다 (Non-goals).
                    merged.update(unopened=True, opened=False, completed=False)
                else:
                    merged["error"] = "attendance_item {}: {}".format(iid, e.code)
                return merged

        if lectures:
            lx.jwt()          # 스레드 진입 전에 미리 확보
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                entry["items"].extend(pool.map(fetch, lectures))

        try:
            entry["announcements"] = scan_announcements(cv, cid)
        except net.HttpError as e:
            entry["errors"].append("announcements: " + str(e))
            entry["announcements"] = []

        entry["items"].sort(key=lambda x: (x.get("week") or 99,
                                           x.get("position") or 0))
        if verbose:
            lec_items = [i for i in entry["items"] if i.get("kind") == "lecture"]
            done = sum(1 for i in lec_items if i.get("completed"))
            unopened = sum(1 for i in lec_items if i.get("unopened"))
            errs = [i for i in lec_items if i.get("error")]
            print("  {:<22} 주차{:>3} · 강의{:>3} (완료 {} · 미개봉 {}) · 평가{:>2}{}".format(
                c["stem"], len(entry["weeks"]), len(lec_items), done, unopened,
                len(graded),
                "  ⚠ " + "; ".join(entry["errors"] + ["조회실패 %d건" % len(errs)][:1 if errs else 0])
                if (entry["errors"] or errs) else ""))

    if prev:
        state.save(PREV, prev)
    state.save(SNAPSHOT, snap)
    state.touch_sync("sync")
    return snap
