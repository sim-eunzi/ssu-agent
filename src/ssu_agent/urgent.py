# -*- coding: utf-8 -*-
"""마감 D-N 이내인데 **아직 안 끝난 것**만 고른다.

`study.py due` 는 vault 표의 `⬜/✅` 를 본다. 진짜 진도(`progress`/`duration`)는
스냅샷에만 있어서, vault 매핑이 실패한 항목(`state/skipped.json` 의
`ambiguous_canvas` — vault 는 주차당 퀴즈 1행인데 Canvas 는 4개)이 영영 `⬜` 로
남고 아침마다 **이미 끝낸 퀴즈를 재촉했다.** 여기는 스냅샷을 직접 본다.

🔴 **'진도율 100% 아님' 은 `completed == false` 다.** LearningX 가 출석을 찍으면
98.7% 여도 다시 볼 이유가 없다 (4차산업 1주차 1차시 실측 · 2026-09-14).

선정만 한다. 렌더는 `brief.urgent`, 전송은 헤르메스봇이다.
"""

from datetime import timedelta

from .risk import (deadline_of, estimate_duration, remaining_seconds,
                   unlock_of)

# 접지 않는 유형 — 진도율이 없고 하나하나가 별개 제출물이다.
PER_ITEM = ("quiz", "assignment", "discussion")


def select(snapshot, now, days=2):
    """창 안에서 아직 안 끝난 항목. 마감 순.

    제외 셋 — ① 이미 완료 ② 창 밖(지난 것 포함) ③ 아직 안 열린 주차.
    ③ 이 없으면 다음 주 강의가 '지금 밀린 것'으로 잡혀 숫자가 부푼다.
    """
    end = now + timedelta(days=days)
    courses = snapshot.get("courses") or {}
    all_items = [it for c in courses.values() for it in (c.get("items") or [])]
    global_est = estimate_duration(all_items, min_sample=1)

    out = []
    for c in courses.values():
        weeks = c.get("weeks") or {}
        est = estimate_duration(c.get("items") or []) or global_est
        for it in (c.get("items") or []):
            if it.get("completed"):
                continue
            dl = deadline_of(it, weeks)
            if not dl or not (now <= dl <= end):
                continue
            unlock = unlock_of(it, weeks)
            if unlock and unlock > now:
                continue
            out.append({
                "item_id": it.get("item_id") or it.get("content_id"),
                "stem": c.get("stem"),
                "week": it.get("week"),
                "kind": it.get("kind"),
                "title": it.get("title") or it.get("lx_title") or "",
                "deadline": dl,
                "remaining_sec": remaining_seconds(it, est),
                "unopened": bool(it.get("unopened")),
            })
    out.sort(key=lambda r: (r["deadline"], r["stem"] or "",
                            r["week"] or 0, r["item_id"] or 0))
    return out


def fold(rows):
    """강의는 과목×주차로 접는다. 퀴즈·과제·토론은 항목 그대로.

    vault 가 주차당 1행인 것과 같은 이유다 — 항목을 그대로 펴면 개강주 유예 때
    아침에 23줄이 나온다(`study.py due` 실측).
    """
    folded, index = [], {}
    for r in rows:
        if r["kind"] in PER_ITEM:
            folded.append({"deadline": r["deadline"], "stem": r["stem"],
                           "week": r["week"], "kind": r["kind"], "count": 1,
                           "remaining_sec": 0.0, "estimated": False,
                           "title": r["title"]})
            continue
        key = (r["stem"], r["week"])
        cur = index.get(key)
        if cur is None:
            cur = {"deadline": r["deadline"], "stem": r["stem"],
                   "week": r["week"], "kind": "lecture", "count": 0,
                   "remaining_sec": 0.0, "estimated": False, "title": None}
            index[key] = cur
            folded.append(cur)
        cur["count"] += 1
        cur["remaining_sec"] += r["remaining_sec"]
        cur["estimated"] = cur["estimated"] or r["unopened"]
        cur["deadline"] = min(cur["deadline"], r["deadline"])
    folded.sort(key=lambda r: (r["deadline"], r["stem"] or "", r["week"] or 0))
    return folded
