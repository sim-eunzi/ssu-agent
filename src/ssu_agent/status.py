# -*- coding: utf-8 -*-
"""요약 현황 — 장부를 세기만 한다. **네트워크도 LLM 도 안 탄다.**

2026-09-02 에 429 로 5건이 실패했을 때 `state/cron.log` 를 직접 뒤져야 했다.
사람이 "무엇이 얼마나 남았는지" 보고 고를 수 있어야 요약이 예측 가능해진다.

🔴 **세는 근거는 `summarize._targets` 하나다.** 여기서 materials/ 를 따로 훑으면
화면이 "2건"이라 하고 실행이 3건을 요약하는 어긋남이 난다 — 대시보드에서 장부
계약이 세 군데로 흩어져 NFC 를 한쪽만 빠뜨렸을 때 났던 것과 같은 병이다.

`미요약` 은 **받아둔 것 중 안 된 것**이다. 아직 안 받은 자료는 materials/ 에
없어서 _targets 가 모른다 — 미공개 자료 수는 `materials --dry-run` 의 소관이다.
"""

from . import summarize

_EMPTY = {"done": 0, "pending": 0, "failed": 0, "unsupported": 0}


def _count(semester, root, sources):
    c = dict(_EMPTY)
    last = None
    for wd, cid, fname, _meta in summarize._targets(semester, root, sources):
        rec = summarize.load_progress_for(wd, cid, fname)
        s = rec.get("status")
        if s == "done":
            c["done"] += 1
        elif s == "unsupported_scanned":
            c["unsupported"] += 1
        elif s == "failed":
            c["failed"] += 1
        else:
            # 없음 · in_progress — 중단된 것은 다음 실행이 이어받으므로 '남은 것'이다
            c["pending"] += 1
        at = rec.get("updated_at")
        if at and (last is None or at > last):
            last = at
    return c, last


def collect(semester, root=None):
    """순수 함수. 장부만 읽어 dict 를 낸다."""
    ledger, l1 = _count(semester, root, ("ledger",))
    manual, l2 = _count(semester, root, ("manual",))
    last = max([x for x in (l1, l2) if x], default=None)
    return {"semester": semester, "ledger": ledger, "manual": manual,
            "video": None,                 # Phase 4 (스펙 §8.2)
            "last_progress_at": last}


def _line(label, c):
    s = "  {}   완료 {} · 미요약 {} · 실패 {}".format(
        label, c["done"], c["pending"], c["failed"])
    if c["unsupported"]:
        s += " · 스캔불가 {}".format(c["unsupported"])
    return s


def _fmt_ts(v):
    """표시용 다듬기 전용. `collect()` 가 내는 원본 `summarize.now()` 형식
    (예: "2026-09-05T03:14:03+09:00") 을 "2026-09-05 03:14" 로 자른다.
    모양이 다르면 원본을 그대로 낸다 — 화면 문구 때문에 죽지 않는다.
    """
    try:
        if (len(v) >= 16 and v[4] == "-" and v[7] == "-"
                and v[10] == "T" and v[13] == ":"):
            return v[:16].replace("T", " ")
    except (TypeError, IndexError):
        pass
    return v


def render(st):
    """코코봇이 그대로 보내는 텍스트. 마크업은 안 붙인다 (brief 규약)."""
    lines = ["📊 요약 현황 ({})".format(st["semester"]),
             _line("자동 수집", st["ledger"]),
             _line("직접 올림", st["manual"])]
    if st.get("video"):
        lines.append(_line("동영상   ", st["video"]))
    if st.get("last_progress_at"):
        lines.append("  마지막 장부 갱신 {}".format(_fmt_ts(st["last_progress_at"])))
    return "\n".join(lines) + "\n"


def pending_line(st):
    """refresh 보고 끝에 붙는 한 줄. 장부만 읽으므로 공짜다.

    🔴 `failed` 도 세야 한다 — `summarize.run` 은 실패한 문서를 다음 실행에서
    재시도하며 돈을 쓴다. pending 만 보면 2026-09-02 429 사고처럼 화면은
    "미요약 없음"인데 다음 실행이 조용히 청구한다.
    """
    a, b = st["ledger"]["pending"], st["manual"]["pending"]
    f = st["ledger"]["failed"] + st["manual"]["failed"]
    if not a and not b and not f:
        return "📊 미요약 없음"
    line = ("📊 미요약 — 자동 수집 {}건 · 직접 올림 {}건   "
            "「요약해줘」 라고 하면 골라서 돌린다".format(a, b))
    if f:
        line += " · 실패 {}건(다음 실행이 재시도)".format(f)
    return line
