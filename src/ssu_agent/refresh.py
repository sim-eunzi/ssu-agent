# -*- coding: utf-8 -*-
"""`refresh` — 수집·반영·자료·요약을 한 번에.

**코코봇의 입구다.** 은지가 *"최신 LMS 업데이트해줘"* 라고 하면 스킬은 이 명령
하나를 부른다. `univ-save` 와 같은 규약이다 — **스킬은 로직이 0**, 순서·중단·
보고는 전부 여기가 진다. 순서를 스킬에 적어두면 스킬과 cron 이 따로 늙는다.

`bin/ssu-agent-cron` · `bin/ssu-agent-materials-cron` 이 셸로 하던 일과 같다.
셸에 있던 규칙을 코드로 올렸으므로 **테스트가 붙는다.**

🔴 **sync 가 문지기다.** 실패하면 뒤를 전부 접는다 — 낡은 스냅샷으로 vault 를
고치면 마감이 되돌아간다. 나머지 단계는 서로 독립이라, 자료 하나가 실패해도
요약은 돈다.
"""

STEPS = ("sync", "vault", "materials", "summary")

LABEL = {"sync": "① 수집   ", "vault": "② vault  ",
         "materials": "③ 자료   ", "summary": "④ 요약   "}


def run(fns, want=STEPS):
    """`fns[단계]()` 를 순서대로 부른다. 각 호출은 `{"ok", "line"}` 을 준다.

    호출 가능한 것을 주입받는다 — 테스트가 네트워크·LLM 없이 순서와 중단 규칙만
    검증할 수 있어야 하기 때문이다.
    """
    bad = [w for w in want if w not in STEPS]
    if bad:
        raise ValueError("모르는 단계: " + ", ".join(bad))

    out = {"steps": [], "aborted": False}
    for name in STEPS:
        if name not in want:
            continue
        try:
            r = fns[name]()
            rec = {"name": name, "ok": bool(r.get("ok", True)),
                   "line": r.get("line") or ""}
        except Exception as e:                  # 한 단계가 죽어도 보고는 나와야 한다
            rec = {"name": name, "ok": False,
                   "line": "{}: {}".format(type(e).__name__,
                                           str(e).split("\n")[0][:160])}
        out["steps"].append(rec)
        if name == "sync" and not rec["ok"]:
            out["aborted"] = True
            break
    return out


# 봇이 은지에게 그대로 보내는 문장이다. 스택 조각·URL·JSON 을 흘리지 않는다.
_TRANSLATE = (
    ("401", "Canvas 토큰이 만료됐어. 재발급해서 ssu-agent/.env 에 넣어야 해"),
    ("403", "Canvas 가 접근을 막았어. 토큰 권한을 확인해줘"),
    ("URLError", "인터넷 연결이 안 돼. 잠시 후 다시 해줄게"),
    ("timed out", "LMS 응답이 없어. 잠시 후 다시 해줄게"),
    ("Timeout", "LMS 응답이 없어. 잠시 후 다시 해줄게"),
)


def explain(line):
    """모르는 오류는 **그대로 넘긴다.** 삼키면 원인을 못 찾는다."""
    for needle, msg in _TRANSLATE:
        if needle in line:
            return msg
    return line


def render(res):
    """코코봇이 그대로 보내는 텍스트. 마크업은 안 붙인다 — HTML 이냐 Markdown
    이냐는 보내는 쪽이 정한다 (README 규약)."""
    lines = ["LMS 업데이트"]
    for s in res["steps"]:
        mark = "" if s["ok"] else "⚠️ "
        text = s["line"] if s["ok"] else explain(s["line"])
        lines.append("{}{}{}".format(LABEL[s["name"]], mark, text))
    if res["aborted"]:
        lines.append("")
        lines.append("수집이 실패해서 여기서 멈췄다 — 낡은 자료로 vault 를 "
                     "고치면 마감이 되돌아간다. 잠시 후 다시 해줘.")
    return "\n".join(lines) + "\n"
