"""LMS 수집 → state/snapshot.json.

Canvas 모듈(주차 구조·퀴즈/과제 마감) + LearningX lessons(주차 마감) +
attendance_items(진도·영상 길이) 를 한 스냅샷으로 합친다.

🔴 조회할 강의 id 는 `attendance_items/summary` 가 정한다. 모듈
`external_url` 의 `.../items/view/{id}` 는 과목을 복제하면 옛 id 가 그대로
남아 404 를 준다 — 2026-09-05 이전에는 그 404 를 "안 봤다"로 읽어 완료·출석이
통째로 유실되고 있었다(확장현실 15/15, 정치철학 53/60, 4차산업 48/52,
한반도 35/37). 목록 엔드포인트 `attendance_items` 는 500 이지만 `/summary`
는 200 이라 N+1 은 여전하되 **틀린 id 는 안 부른다.**

퀴즈·과제 제출 여부는 Canvas `students/submissions` 로 본다. `practice_quiz`
는 assignment 가 없어 거기 안 나오므로 `quizzes/{id}/submission` 을 따로 본다.

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


# --------------------------------------------------- 아이템 id 확정 (권위)
def lecture_records(summary_ids, module_lectures):
    """`summary` 의 id 를 권위로 삼아 강의 아이템 목록을 만든다.

    🔴 모듈 `external_url` 의 id 를 그대로 믿으면 안 된다. 과목을 복제하면
    옛 id 가 URL 에 남아 `attendance_items/{id}` 가 404 를 준다 —
    2026-09-05 실측에서 404 개수가 '겹치지 않는 id 개수'와 전 과목 정확히
    일치했다. 옛 해석("404 = 안 봤다")은 틀렸다.

    - summary 에 있는 id 만 조회한다 (없는 id 는 404 를 부르러 가지 않는다)
    - id 가 맞는 과목(선형대수·3code)은 모듈 메타를 그대로 얹어 기존 동작 유지
    - id 가 안 맞으면 주차·제목을 LearningX 응답에서 채운다 (backfill_from_lx)
    """
    by_id = {}
    for rec in module_lectures or []:
        iid = rec.get("item_id")
        if iid is not None:
            by_id[int(iid)] = rec
    out = []
    for iid in sorted(int(i) for i in summary_ids or []):
        base = dict(by_id.get(iid) or {})
        base.update(item_id=iid, kind="lecture")
        out.append(base)
    return out


MATERIAL_CODE_RE = re.compile(r"_(\d{2})(\d{2})_")
MAX_WEEK = 16


def week_from_title(title):
    """제목에서 주차를 읽는 **최후의 폴백.**

    LearningX 도 Canvas 도 주차를 안 주는 자료가 있다 — 4차산업 11~14주차
    학습자료 4건(실측). 주차가 없으면 `materials` 가 그 파일을 통째로
    건너뛰므로 자료가 영영 안 받아진다.

    1) `N주차`
    2) `_WWLL_` 파일 코드 (`_1101_` = 11주차 1교시). 오탐을 막으려 1~16 만.
    """
    if not title:
        return None
    m = WEEK_RE.search(title)
    if m:
        wk = int(m.group(1))
        if 1 <= wk <= MAX_WEEK:
            return wk
    m = MATERIAL_CODE_RE.search(title)
    if m:
        wk = int(m.group(1))
        if 1 <= wk <= MAX_WEEK:
            return wk
    return None


def backfill_from_lx(item, week_names=None):
    """Canvas 모듈에서 못 얻은 주차·제목을 LearningX 응답으로 메운다.

    Canvas 값이 있으면 그것이 이긴다 — 모듈 이름이 사람이 읽는 이름이다.
    """
    week_names = week_names or {}
    if item.get("week") is None and item.get("lx_week") is not None:
        try:
            item["week"] = int(item["lx_week"])
        except (TypeError, ValueError):
            pass
    if not item.get("title") and item.get("lx_title"):
        item["title"] = item["lx_title"]
    if item.get("week") is None:
        wk = week_from_title(item.get("title"))
        if wk is not None:
            item["week"] = wk
    if not item.get("module"):
        name = week_names.get(item.get("week"))
        if name:
            item["module"] = name
    return item


# ------------------------------------------------- 퀴즈·과제 제출 여부
DONE_STATES = ("submitted", "graded", "complete", "pending_review")


def _sub_done(sub):
    if not sub:
        return False
    if sub.get("submitted_at"):
        return True
    return sub.get("workflow_state") in DONE_STATES


def _quiz_done(subs):
    for q in subs or []:
        if q.get("finished_at") or q.get("workflow_state") in DONE_STATES:
            return True
    return False


def completion_index(assignments, submissions, quiz_submissions):
    """(kind, content_id) → 제출했는가.

    모듈 아이템의 `content_id` 는 퀴즈면 quiz_id, 과제면 assignment id,
    토론이면 topic id 라 셋을 따로 이어줘야 한다. `practice_quiz` 는
    assignment 자체가 없으므로 `quiz_submissions` 가 유일한 근거다.
    """
    subs = {s.get("assignment_id"): s for s in submissions or []}
    out = {}
    for a in assignments or []:
        done = _sub_done(subs.get(a.get("id")))
        out[("assignment", a.get("id"))] = done
        if a.get("quiz_id"):
            out[("quiz", a["quiz_id"])] = done
        topic = a.get("discussion_topic") or {}
        if topic.get("id"):
            out[("discussion", topic["id"])] = done
    for qid, qsubs in (quiz_submissions or {}).items():
        if _quiz_done(qsubs):
            out[("quiz", qid)] = True
        else:
            out.setdefault(("quiz", qid), False)
    return out


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

        # 퀴즈·과제 제출 여부. 예전에는 아예 조회하지 않아 항상 미완료였다.
        try:
            asg = cv.assignments(cid)
            subs = cv.my_submissions(cid)
            have = {a.get("quiz_id") for a in asg if a.get("quiz_id")}
            qsubs = {}
            for g in graded:
                if g.get("kind") == "quiz" and g.get("content_id") not in have:
                    try:                       # practice_quiz — assignment 가 없다
                        qsubs[g["content_id"]] = cv.quiz_submission(cid, g["content_id"])
                    except net.HttpError:
                        pass
            done_idx = completion_index(asg, subs, qsubs)
        except net.HttpError as e:
            entry["errors"].append("submissions: " + str(e))
            done_idx = {}
        for g in graded:
            g["completed"] = bool(done_idx.get((g.get("kind"), g.get("content_id"))))
        entry["items"].extend(graded)

        try:
            entry["weeks"] = {str(k): v for k, v in
                              parse_lessons(lx.lessons(cid)).items()}
        except (net.HttpError, RuntimeError) as e:
            entry["errors"].append("lessons: " + str(e))

        # 🔴 조회할 강의 id 는 summary 가 정한다 (모듈 URL 의 id 는 낡을 수 있다)
        try:
            summary_ids = list(lx.attendance_summary(cid))
            lectures = lecture_records(summary_ids, lectures)
        except (net.HttpError, RuntimeError) as e:
            entry["errors"].append("attendance_summary: " + str(e))

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
                got = list(pool.map(fetch, lectures))
            for it in got:
                backfill_from_lx(it, week_names)
            entry["items"].extend(got)

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
