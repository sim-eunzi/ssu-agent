"""study.py 서브프로세스 어댑터 — Canvas 상태를 vault 에 반영한다 (M2).

**vault 에 직접 쓰지 않는다.** `study.py` 가 과목 `.md` 의 단일 writer 다
(핸드오프 §4). 여기서는 차이를 계산하고 CLI 를 부르기만 한다.

동기화 규칙 (2026-09-02 확정):

- `완료` 는 **단방향** — `⬜` → `✅` 만. Canvas 가 미완료여도 vault 의 `✅` 는
  건드리지 않는다. 사람이 코코봇으로 체크한 걸 기계가 지우면 안 된다.
- `마감` 은 **양방향** — 앞당김도 밀림도 반영한다. 연장이 더 흔하고, vault 가
  옛 마감을 들고 있으면 `study.py today` 가 "오늘 마감"이라고 잘못 알린다.
  **단 `✅` 행은 제외** — `open_deadlines`(`study.py:262`)가 이미 완료 행을
  빼므로 알림에 영향이 없고, 갱신하면 "언제까지였던 걸 언제 했는지"만 흐려진다.
- vault 에만 있는 행은 **손대지 않는다.** K-MOOC 3과목 · 사람이 손으로 넣은 것 ·
  `seed_univ.py` 가 강의계획서에서 만든 미래 항목이 섞여 있다. `rm` 을 쓰지 않는다.
- `비고` 에 쓰지 않는다. `--note` 는 셀 전체 교체라 사람이 쓴 것을 덮는다
  (195행 중 71행이 채워져 있다). 추적은 `events.py` diff 와 git log 가 한다.
- 404(`unopened`)는 "안 봤다"지 "했다"가 아니다. `completed` 만 본다.
  (2026-09-05: 404 의 대부분은 사실 **id 가 틀린 것**이었다 — `sync` 가
  `attendance_items/summary` 로 id 를 확정하게 고쳤다. 이제 404 는 드물다.)
"""

import json
import subprocess
from datetime import datetime, timezone

from . import state
from .config import KST, get

# Canvas kind → vault 유형. 토론이 과제인 건 seed_univ 선례다.
KIND_TO_TYPE = {"lecture": "강의", "quiz": "퀴즈",
                "assignment": "과제", "discussion": "과제"}

SKIPPED = "skipped.json"
LOCK_TIMEOUT = "30"          # 초과하면 사이클을 건너뛰고 다음 폴링에 재시도
RC_LOCKED = 3


# ---------------------------------------------------------------- 변환
def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def mmdd(iso):
    """ISO8601 → KST 기준 `MM-DD`. vault 표가 쓰는 형식이다."""
    d = _dt(iso)
    return d.astimezone(KST).strftime("%m-%d") if d else None


def safe(s):
    """`sane()`(study.py:374) 이 거부하는 입력을 미리 걸러낸다.
    우회해서 넣으면 대시보드가 그 행을 조용히 버린다 (lib/vault.js:430)."""
    return bool(s) and not any(ch in s for ch in ("|", "\n", "\r"))


def canvas_rows(entry):
    """스냅샷 과목 entry → {(주차, 유형): 상태}.

    **주차 단위로 접는다 — 유형을 가리지 않는다.** vault 는 주차당 유형 1행
    (강의계획서 단위)인데 Canvas 는 아이템이 여러 개다: 강의 영상이 한나아렌트
    6개/주차·3code 7개/주차, 퀴즈가 4차산업 QUIZ 1~4.

    - 완료 = 그 주차 그 유형을 **전부** 끝냈을 때
    - 마감 = `weeks`(lessons) 의 주차 마감. 미개봉 항목은 404 라 `due_at` 이
      아예 없어서, 아이템에서 뽑으면 대부분 None 이 된다 (핸드오프 §2.2)

    🔴 **퀴즈·과제·토론은 2026-09-14 까지 접지 않았다** — *"vault 행이 여럿이어야
    할 수도 있어 추측이 위험하다"* 는 이유였는데, 그건 Canvas 쪽만 보고 내린
    판단이었다. 어디에 붙일지는 vault 도 같이 보는 `plan()` 이 정한다.
    접지 않던 동안 4차산업 1주차 퀴즈는 QUIZ 1~4 가 전부 완료인데도 vault 에서
    영영 ⬜ 였고, 아침 체크인이 매일 끝낸 퀴즈를 재촉했다.

    **하나짜리는 접기 전과 똑같다** — 제 title·due 를 그대로 쓴다. 강의만은
    개수와 무관하게 접는다(vault 의 'N주차 강의' 명명이 그 전제 위에 있다).
    """
    weeks = entry.get("weeks") or {}
    groups = {}
    for it in entry.get("items") or []:
        typ = KIND_TO_TYPE.get(it.get("kind"))
        wk = it.get("week")
        if typ is None or wk is None:
            continue
        try:
            wk = int(wk)
        except (TypeError, ValueError):
            continue
        groups.setdefault((wk, typ), []).append(it)

    out = {}
    for (wk, typ), its in groups.items():
        if typ != "강의" and len(its) == 1:
            it = its[0]
            out[(wk, typ)] = {"title": it.get("title") or "",
                              "due": mmdd(it.get("due_at")),
                              "completed": bool(it.get("completed")),
                              "unopened": bool(it.get("unopened")),
                              "count": 1}
            continue
        due = mmdd((weeks.get(str(wk)) or {}).get("due_at"))
        if due is None:                       # lessons 가 없으면 아이템 중 가장 늦은 것
            got = [d for d in (_dt(i.get("due_at")) for i in its) if d]
            due = max(got).astimezone(KST).strftime("%m-%d") if got else None
        out[(wk, typ)] = {
            "title": "%d주차 %s" % (wk, typ),  # vault 의 'N주차 퀴즈' 명명과 맞춘다
            "due": due,
            "completed": all(bool(i.get("completed")) for i in its),
            "unopened": any(bool(i.get("unopened")) for i in its),
            "count": len(its),
        }
    return out


def parse_vault(payload):
    """`study.py list --json` 출력 → {(주차, 유형): 행}."""
    out = {}
    for r in (payload or {}).get("rows") or []:
        try:
            wk = int(r.get("week"))
        except (TypeError, ValueError):
            continue
        key = (wk, r.get("type"))
        if key in out:
            out[key]["ambiguous"] = True
            continue
        out[key] = {"due": r.get("due"), "done": bool(r.get("done")),
                    "item": r.get("item"), "ambiguous": False}
    return out


# ---------------------------------------------------------------- 판단
def _skip(stem, wk, typ, reason):
    return {"stem": stem, "week": wk, "type": typ, "reason": reason}


def plan(stem, vault, canvas):
    """(할 일, 건너뛴 것). 차이가 있을 때만 액션을 만든다 — 커밋 노이즈 방지."""
    acts, skips = [], []
    vault_weeks = {w for (w, _t) in vault}
    for (wk, typ) in sorted(canvas):
        c = canvas[(wk, typ)]
        v = vault.get((wk, typ))

        if v is None:                      # Canvas 에만 있다 → 추가
            # 🔴 그 주차 자체가 vault 에 없으면 새 항목이 아니라 **주차 매핑 실패**다.
            #    add 는 locate() 를 안 타서 exit 2 가 안 나므로 여기서 막아야 한다.
            #    (정치철학-한나아렌트는 1~14 + 16 이라 Canvas 15주차가 여기 걸린다)
            if wk not in vault_weeks:
                skips.append(_skip(stem, wk, typ, "week_not_in_vault"))
                continue
            if not safe(c["title"]):
                skips.append(_skip(stem, wk, typ, "unsafe_title"))
                continue
            acts.append({"action": "add", "stem": stem, "week": wk,
                         "type": typ, "item": c["title"], "due": c["due"]})
            continue

        if v["ambiguous"]:                 # vault 에 같은 키가 둘 — 추측하지 않는다
            skips.append(_skip(stem, wk, typ, "ambiguous"))
            continue

        # 완료 — 단방향. unopened(404)는 완료 근거가 아니다.
        if c["completed"] and not v["done"]:
            acts.append({"action": "done", "stem": stem,
                         "week": wk, "type": typ})

        # 마감 — 양방향이되 ✅ 행은 제외
        if not v["done"] and c["due"] and c["due"] != v["due"]:
            acts.append({"action": "set_due", "stem": stem, "week": wk,
                         "type": typ, "due": c["due"]})
    return acts, skips


# ---------------------------------------------------------------- 실행
def run_study(args, timeout=120):
    cfg = get()
    cmd = ["python3", cfg.study_py] + list(args)
    if cfg.vault_path:
        cmd += ["--vault", cfg.vault_path]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def read_vault(stem, run=None):
    run = run or run_study
    rc, out, _err = run(["list", stem, "--json"])
    if rc != 0:
        return {}
    try:
        return parse_vault(json.loads(out))
    except ValueError:
        return {}


def _argv(a, dry_run):
    base = [a["stem"], str(a["week"]), "--type", a["type"],
            "--lock-timeout", LOCK_TIMEOUT]
    if a["action"] == "done":
        cmd = ["done"] + base
    elif a["action"] == "set_due":
        cmd = ["set"] + base + ["--due", a["due"]]
    else:                                   # add
        cmd = ["add"] + base + ["--item", a["item"]]
        if a.get("due"):
            cmd += ["--when", a["due"]]
    return cmd + (["--dry-run"] if dry_run else [])


def _key(a):
    return "%s/%s/%s/%s" % (a["stem"], a["week"], a["type"], a["action"])


def note_skips(skips, store):
    """plan 단계의 스킵을 장부에 남긴다. 같은 항목을 반복 기록하지 않는다."""
    n = 0
    for s in skips:
        k = "%s/%s/%s/%s" % (s["stem"], s["week"], s["type"], s["reason"])
        if k not in store:
            store[k] = {"reason": s["reason"]}
            n += 1
    return n


def apply(actions, run=None, dry_run=False, skipped=None):
    """액션을 순서대로 적용한다. exit 3(락)이면 즉시 중단하고 다음 주기에 맡긴다."""
    run = run or run_study
    store = state.load(SKIPPED) if skipped is None else skipped
    res = {"applied": 0, "skipped": 0, "locked": False, "errors": 0}
    for a in actions:
        k = _key(a)
        if k in store:                      # 같은 항목을 반복 기록·재시도하지 않는다
            continue
        rc, _out, err = run(_argv(a, dry_run))
        if rc == RC_LOCKED:
            res["locked"] = True
            break
        if rc == 0:
            res["applied"] += 1
        elif rc == 2:                       # 행이 없다 / 후보 여럿 — 주차 매핑 실패
            store[k] = {"reason": "study_exit2", "stderr": (err or "").strip()[:200]}
            res["skipped"] += 1
        else:
            res["errors"] += 1
    if skipped is None and not dry_run:
        state.save(SKIPPED, store)
    return res
