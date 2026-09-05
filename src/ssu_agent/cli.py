"""ssu-agent CLI.

    ssu-agent doctor              환경/토큰 점검
    ssu-agent auth                LTI 체인 실측 (JWT 발급 + 남은 시간)
    ssu-agent sync [--full]       LMS 수집 → state/snapshot.json
    ssu-agent brief [kind]        morning|evening|weekly 텍스트
    ssu-agent brief --json        헤르메스봇이 받아갈 구조 (--ack 로 outbox 비움)
    ssu-agent items [과목]        남은 항목 나열
    ssu-agent vault-sync          Canvas 상태를 vault 에 반영 (study.py 경유)
    ssu-agent materials           PDF 자료 내려받기 + 주차 인덱스 갱신 (data/)
    ssu-agent summarize           자료 → 마크다운 → LLM 요약 (--estimate 로 비용 먼저)

**아무것도 전송하지 않는다.** 텔레그램은 헤르메스봇 하나가 담당한다.
이 CLI 는 계산해서 stdout 으로 내놓기만 한다.
"""

import argparse
import contextlib
import io
import json
import sys
import time

from . import brief as brief_mod
from . import canvas as canvas_mod
from . import events, materials, refresh, risk, state, study_cli, summarize, sync
from .config import ROOT, get


def _snapshot(refresh=False, full=False):
    if refresh:
        return sync.run(full=full)
    snap = state.load(sync.SNAPSHOT)
    if not snap:
        raise SystemExit("스냅샷이 없다. 먼저 `ssu-agent sync` 를 돌려라.")
    return snap


# ------------------------------------------------------------- 명령들
def cmd_doctor(a):
    cfg = get()
    w = "{:<17} {}"
    print(w.format("repo", ROOT))
    print(w.format("canvas_base", cfg.canvas_base))
    print(w.format("semester", "{} ({} ~ {})".format(
        cfg.semester, cfg.term_start, cfg.term_end)))
    print(w.format("courses", "{}과목".format(len(cfg.courses))))
    print(w.format("CANVAS_TOKEN",
                   ("✅ " + cfg.canvas_token[:6] + "…") if cfg.canvas_token else "❌ 없음"))
    for k in ("VAULT_PATH", "STUDY_PY"):
        print(w.format(k, getattr(cfg, k.lower()) or "— (M2 에서 필요)"))
    print(w.format("last sync", state.last_sync("sync") or "없음"))
    print(w.format("outbox", "{}건 대기".format(len(state.outbox_peek()))))
    try:
        import faster_whisper                     # noqa: F401
        print(w.format("faster-whisper", "✅"))
    except ImportError:
        print(w.format("faster-whisper",
                       "⚠️ 없음 — transcribe 만 못 쓴다 (pip3 install faster-whisper)"))
    print(w.format("알림", "이 에이전트는 전송하지 않는다 (헤르메스봇 담당)"))
    return 0


def cmd_auth(a):
    lx = canvas_mod.LearningX()
    t0 = time.time()
    tok = lx.jwt(force=a.force)
    seed = state.load("seed.json")
    print("런치 씨앗   course {} · item {}".format(
        seed.get("course_id"), seed.get("item_id")))
    print("JWT         ✅ {}… ({:.1f}s)".format(tok[:24], time.time() - t0))
    print("만료까지    {}분".format(
        int((canvas_mod.jwt_exp(tok) - time.time()) // 60)))
    return 0


def cmd_sync(a):
    cfg = get()
    print("수집 중…", file=sys.stderr)
    prev = state.load(sync.SNAPSHOT)
    snap = sync.run(full=a.full, verbose=True)

    found = events.detect(snap, prev, soon_days=cfg.notify["soon_days"])
    fresh = events.unseen(found)
    if fresh:
        state.outbox_add(fresh)
        state.mark_notified([e["key"] for e in fresh])
    print("\n사건 {}건 · outbox {}건 대기".format(
        len(fresh), len(state.outbox_peek())))
    for e in fresh:
        print("  · " + brief_mod.event_line(e))
    return 0


def cmd_brief(a):
    snap = _snapshot(refresh=a.refresh, full=a.full)
    assessment = risk.assess(snap)
    box = state.outbox_peek()
    if a.json:
        print(json.dumps(brief_mod.payload(assessment, a.kind, box, snap),
                         ensure_ascii=False, indent=1))
    else:
        print(brief_mod.render(assessment, a.kind, box))
    if a.ack:
        state.outbox_clear()
    return 0


def cmd_items(a):
    assessment = risk.assess(_snapshot(refresh=a.refresh))
    for c in assessment["courses"]:
        if a.course and a.course not in (c["stem"], str(c["canvas_id"])):
            continue
        print("\n{} {} — {}개 · {}{} · {} (필요/가용 {})".format(
            c["icon"], c["stem"], c["pending_count"],
            "≈" if c.get("estimated") else "",
            risk.fmt_hours(c["remaining_hours"]), c["level"], c["ratio"]))
        for p in c["pending"]:
            dt = risk.parse_dt(p["deadline"])
            print("   {:>2}주 {:<42} {}{:>7}  {}".format(
                p["week"] or "?", (p["title"] or "")[:42],
                "≈" if p.get("unopened") else " ",
                risk.fmt_hours(p["remaining_sec"] / 3600.0),
                dt.strftime("%m/%d %H:%M") if dt else "마감미정"))
    if assessment["overdue"]:
        print("\n마감 지남 {}건".format(len(assessment["overdue"])))
        for r in assessment["overdue"][:20]:
            print("   {} {}".format(r["stem"], (r["title"] or "")[:50]))
    return 0


def cmd_vault_sync(a):
    """Canvas → vault. study.py 를 서브프로세스로 부른다 — 직접 쓰지 않는다."""
    cfg = get()
    missing = cfg.missing("STUDY_PY", "VAULT_PATH")
    if missing:
        raise SystemExit("환경변수가 비었다: " + ", ".join(missing))
    snap = _snapshot(refresh=a.refresh)
    store = state.load(study_cli.SKIPPED)
    tot = {"applied": 0, "skipped": 0, "errors": 0}
    locked = False

    for _cid, entry in sorted(snap.get("courses", {}).items()):
        stem = entry.get("stem")
        vault = study_cli.read_vault(stem)
        if not vault:
            print("  {:20s} vault 를 못 읽었다 — 건너뜀".format(stem))
            continue
        acts, skips = study_cli.plan(stem, vault, study_cli.canvas_rows(entry))
        study_cli.note_skips(skips, store)
        if not acts and not skips:
            continue
        print("  {:20s} {}건{}".format(
            stem, len(acts), "  (스킵 %d)" % len(skips) if skips else ""))
        for x in acts:
            print("      {action:8s} {week}주차 {type}{extra}".format(
                extra=(" → " + x["due"]) if x.get("due") else "", **x))
        res = study_cli.apply(acts, dry_run=a.dry_run, skipped=store)
        for k in ("applied", "skipped", "errors"):
            tot[k] += res[k]
        if res["locked"]:
            locked = True
            print("  ⏸ 락 대기 초과 — 사이클을 건너뛴다. 다음 주기에 재시도")
            break

    if not a.dry_run:
        state.save(study_cli.SKIPPED, store)
    _sum("{} 적용 {applied} · 스킵 {skipped} · 오류 {errors}{}".format(
        "[dry-run]" if a.dry_run else "", "  ⏸ 락" if locked else "", **tot))
    return 0


def cmd_materials(a):
    """개봉된 강의의 PDF 만. 미개봉은 404 라 자료 정보 자체가 없다 (§2.2)."""
    snap = _snapshot(refresh=a.refresh)
    res = materials.run(snap, snap.get("semester") or get().semester,
                        dry_run=a.dry_run)
    _sum("{} 저장 {saved} · 건너뜀 {skipped} · 실패 {failed} · {mb:.1f}MB".format(
        "[dry-run]" if a.dry_run else "", mb=res["bytes"] / 1048576.0, **res))
    n = materials.write_index(snap, snap.get("semester") or get().semester,
                              dry_run=a.dry_run)
    print("주차 인덱스 {}개 갱신 (대시보드가 읽는 meta.json)".format(n))
    pend = materials.not_ready(snap)
    if not a.dry_run:
        # stdout 은 cron 로그에 묻힌다. 사유는 파일로 남겨야 이어받는다.
        materials.write_skipped(snap, str(state._path("materials_skipped.json")))
    by = {}
    for x in pend:
        by.setdefault(x["reason"], []).append(x)
    LABEL = {"not_open": "아직 공개 전 — 주차가 풀리면 받는다",
             "unsupported_type": "PDF 가 아니라 못 받는다 (Commons 뷰어 밖)"}
    for reason, xs in sorted(by.items()):
        print("{} {}건 (실패 아님)".format(LABEL.get(reason, reason), len(xs)))
        for x in xs[:4]:
            print("    {stem} W{week:02d} {file}".format(**x))
        if len(xs) > 4:
            print("    … 외 {}건".format(len(xs) - 4))
    return 0


def cmd_summarize(a):
    """자료 요약. --estimate 는 키 없이 돌아 비용만 어림한다."""
    cfg = get()
    sem = cfg.semester
    if a.models:
        try:
            for m in summarize.list_models():
                print("  " + m)
        except Exception as e:                      # 인증 실패를 트레이스백으로 뱉지 않는다
            raise SystemExit("모델 목록을 못 받았다 — %s: %s"
                             % (type(e).__name__, str(e).split("\n")[0][:160]))
        return 0
    if a.estimate:
        e = summarize.estimate(sem)
        _sum("[예상] 문서 {docs}개 · {chars:,}자 · 호출 {chunks}회 · ${est_usd}"
             .format(**e))
        print("문서 {docs}개 · {chars:,}자 · 호출 {chunks}회 예상".format(**e))
        print("  스캔 PDF {unsupported}개 제외 · 이미 된 것 {skipped}개".format(**e))
        print("  입력 ~{est_input_tokens:,}토큰 · 출력 ~{est_output_tokens:,}토큰"
              .format(**e))
        print("  예상 ${est_usd} ({})  ※ 눈대중이다".format(summarize.MODEL, **e))
        return 0
    try:
        res = summarize.run(sem, max_calls=a.limit)
    except Exception as e:
        raise SystemExit("요약 실패 — %s: %s"
                         % (type(e).__name__, str(e).split("\n")[0][:160]))
    _sum("요약 {done} · 건너뜀 {skipped} · 스캔 {unsupported} · 실패 {failed} "
         "· 호출 {calls}회{}".format("  ⏸ 상한" if res["budget_hit"] else "", **res))
    if res.get("in_tokens") or res.get("out_tokens"):
        print("  실제 토큰 — 입력 {in_tokens:,} · 출력 {out_tokens:,}".format(**res))
    if res["budget_hit"]:
        print("  남은 것은 .progress/ 에 있다 — 다음 실행이 이어받는다")
    return 0


SUMMARY = []


def _sum(s):
    """찍으면서 동시에 '이게 이 명령의 요약 줄' 이라고 표시한다.

    refresh 가 마지막 줄을 긁어가던 방식은 틀렸다 — cmd_materials 는 요약
    뒤에 미공개 목록을 더 찍어서 ZIP 파일명이 요약 자리에 올라왔다.
    긁지 말고 **찍는 쪽이 알려준다.**
    """
    SUMMARY.append(s)
    print(s)


class _Args(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _quiet(fn, args):
    """기존 cmd_* 를 그대로 부르고 **마지막 요약 줄**만 가져온다.

    형식을 여기서 다시 짜면 출력이 두 벌 생겨 따로 늙는다. cmd_* 들은 이미
    끝줄에 한 줄 요약을 찍으므로 그걸 쓴다. 상세는 --verbose 로 흘린다.
    """
    del SUMMARY[:]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(args)
    detail = buf.getvalue()
    if SUMMARY:
        line = " · ".join(x.strip() for x in SUMMARY)
    else:
        lines = [x for x in detail.splitlines() if x.strip()]
        line = lines[-1].strip() if lines else "(출력 없음)"
    return {"ok": True, "line": line, "detail": detail}


def cmd_refresh(a):
    """수집 → vault → 자료 → 요약. 코코봇이 부르는 입구다 (refresh.py 참고)."""
    want = list(refresh.STEPS)
    if a.no_summary:
        want.remove("summary")
    if a.no_materials:
        want = [w for w in want if w not in ("materials", "summary")]

    fresh = _Args(refresh=False, dry_run=a.dry_run, verbose=False)

    def _sync():
        snap = sync.run(verbose=False)
        nc = len(snap.get("courses") or {})
        ni = sum(len(e.get("items") or [])
                 for e in (snap.get("courses") or {}).values())
        errs = [x for e in (snap.get("courses") or {}).values()
                for x in (e.get("errors") or [])]
        # 과목이 하나도 안 잡혔으면 수집 실패다 — 뒤를 돌리면 안 된다.
        return {"ok": nc > 0,
                "line": "{}과목 {}항목{}".format(
                    nc, ni, "  ⚠️ 오류 %d건" % len(errs) if errs else "")}

    fns = {
        "sync": _sync,
        "vault": lambda: _quiet(cmd_vault_sync, fresh),
        "materials": lambda: _quiet(cmd_materials, fresh),
        # 🔴 dry-run 은 estimate 로 간다. 안 그러면 "확인만 할게" 가 돈을 쓴다.
        "summary": lambda: _quiet(cmd_summarize, _Args(
            models=False, estimate=a.dry_run, limit=a.limit)),
    }
    res = refresh.run(fns, want=tuple(want))
    if a.verbose:
        for s in res["steps"]:
            print("--- {} ---".format(s["name"]))
            print(s.get("detail") or s["line"])
    sys.stdout.write(refresh.render(res))
    return 1 if res["aborted"] else 0


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(
        prog="ssu-agent", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    au = sub.add_parser("auth")
    au.add_argument("--force", action="store_true", help="캐시 무시하고 재발급")
    au.set_defaults(func=cmd_auth)

    sy = sub.add_parser("sync")
    sy.add_argument("--full", action="store_true", help="완료 항목도 다시 조회")
    sy.set_defaults(func=cmd_sync)

    br = sub.add_parser("brief")
    br.add_argument("kind", nargs="?", default="morning",
                    choices=sorted(brief_mod.RENDERERS))
    br.add_argument("--json", action="store_true", help="구조화 출력 (헤르메스봇용)")
    br.add_argument("--ack", action="store_true",
                    help="출력 후 outbox 를 비운다. 전송에 성공한 쪽이 부른다")
    br.add_argument("--refresh", action="store_true", help="먼저 sync 한다")
    br.add_argument("--full", action="store_true")
    br.set_defaults(func=cmd_brief)

    vs = sub.add_parser("vault-sync")
    vs.add_argument("--dry-run", action="store_true",
                    help="study.py 에 --dry-run 을 붙여 부른다. vault 무변경")
    vs.add_argument("--refresh", action="store_true", help="먼저 sync 한다")
    vs.set_defaults(func=cmd_vault_sync)

    ma = sub.add_parser("materials")
    ma.add_argument("--dry-run", action="store_true", help="받을 목록만 보인다")
    ma.add_argument("--refresh", action="store_true", help="먼저 sync 한다")
    ma.set_defaults(func=cmd_materials)

    su = sub.add_parser("summarize")
    su.add_argument("--models", action="store_true",
                    help="이 키로 쓸 수 있는 모델 목록")
    su.add_argument("--estimate", action="store_true",
                    help="키 없이 문자·토큰·예상비용만 낸다")
    su.add_argument("--limit", type=int, default=summarize.MAX_CALLS,
                    help="이번 실행의 LLM 호출 상한 (기본 %d)" % summarize.MAX_CALLS)
    su.set_defaults(func=cmd_summarize)

    rf = sub.add_parser("refresh", help="수집→vault→자료→요약 한 번에 (코코봇 입구)")
    rf.add_argument("--no-summary", action="store_true",
                    help="LLM 요약 건너뛰기 (비용 0)")
    rf.add_argument("--no-materials", action="store_true",
                    help="자료 다운로드·요약 건너뛰기")
    rf.add_argument("--limit", type=int, default=None, help="요약 LLM 호출 상한")
    rf.add_argument("--dry-run", action="store_true")
    rf.add_argument("--verbose", action="store_true", help="각 단계 원문 출력")
    rf.set_defaults(func=cmd_refresh)

    it = sub.add_parser("items")
    it.add_argument("course", nargs="?", help="vault stem 또는 Canvas ID")
    it.add_argument("--refresh", action="store_true")
    it.set_defaults(func=cmd_items)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, SystemExit) as e:
        print("✖ {}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
