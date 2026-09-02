"""ssu-agent CLI.

    ssu-agent doctor              환경/토큰 점검
    ssu-agent auth                LTI 체인 실측 (JWT 발급 + 남은 시간)
    ssu-agent sync [--full]       LMS 수집 → state/snapshot.json, 즉시 알림 발송
    ssu-agent report [kind]       morning|evening|weekly 를 표준출력으로
    ssu-agent notify <kind>       위 브리핑을 텔레그램으로 발송
    ssu-agent items <stem|cid>    한 과목의 남은 항목 나열

공통 옵션: --dry-run (발송 대신 출력), --verbose
"""

import argparse
import sys
import time

from . import canvas as canvas_mod
from . import events, notify, risk, state, sync
from .config import get


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
    from .config import ROOT
    w = "{:<17} {}"
    print(w.format("repo", ROOT))
    print(w.format("canvas_base", cfg.canvas_base))
    print(w.format("semester", "{} ({} ~ {})".format(
        cfg.semester, cfg.term_start, cfg.term_end)))
    print(w.format("courses", "{}과목".format(len(cfg.courses))))
    for k in ("CANVAS_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        v = getattr(cfg, k.lower())
        print(w.format(k, ("✅ " + v[:6] + "…") if v else "❌ 없음"))
    for k in ("VAULT_PATH", "STUDY_PY"):
        print(w.format(k, getattr(cfg, k.lower()) or "— (M2 에서 필요)"))
    print(w.format("last sync", state.last_sync("sync") or "없음"))
    return 0


def cmd_auth(a):
    cfg = get()
    lx = canvas_mod.LearningX(cfg=cfg)
    t0 = time.time()
    tok = lx.jwt(force=a.force)
    left = canvas_mod.jwt_exp(tok) - time.time()
    seed = state.load("seed.json")
    print("런치 씨앗   course {} · item {}".format(
        seed.get("course_id"), seed.get("item_id")))
    print("JWT         ✅ {}… ({:.1f}s)".format(tok[:24], time.time() - t0))
    print("만료까지    {}분".format(int(left // 60)))
    return 0


def cmd_sync(a):
    cfg = get()
    print("수집 중…")
    prev = state.load(sync.SNAPSHOT)
    snap = sync.run(full=a.full, verbose=True)

    found = events.detect(snap, prev, soon_days=cfg.notify["soon_days"])
    fresh = events.unseen(found)
    print("\n사건 {}건 (신규 {}건)".format(len(found), len(fresh)))
    sent = []
    for e in fresh:
        msg = notify.event_message(e)
        notify.send(msg, cfg, dry_run=a.dry_run)
        sent.append(e["key"])
        print("  → " + e["type"] + " " + (e.get("title") or ""))
    if sent and not a.dry_run:
        state.mark_notified(sent)
    return 0


def _assess(a):
    return risk.assess(_snapshot(refresh=a.refresh, full=getattr(a, "full", False)))


def _compose(kind, assessment):
    if kind == "morning":
        return notify.morning(assessment)
    if kind == "evening":
        return notify.evening(assessment)
    if kind == "weekly":
        return notify.weekly(assessment)
    raise SystemExit("알 수 없는 종류: " + kind)


def cmd_report(a):
    print(_compose(a.kind, _assess(a)))
    return 0


def cmd_notify(a):
    msg = _compose(a.kind, _assess(a))
    notify.send(msg, dry_run=a.dry_run)
    if a.dry_run:
        return 0
    print("발송 완료: " + a.kind)
    return 0


def cmd_items(a):
    cfg = get()
    snap = _snapshot(refresh=a.refresh)
    assessment = risk.assess(snap)
    target = a.course
    for c in assessment["courses"]:
        if target and target not in (c["stem"], str(c["canvas_id"])):
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


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="ssu-agent", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="발송하지 않고 출력만")
    p.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    a = sub.add_parser("auth")
    a.add_argument("--force", action="store_true", help="캐시 무시하고 재발급")
    a.set_defaults(func=cmd_auth)

    s = sub.add_parser("sync")
    s.add_argument("--full", action="store_true", help="완료 항목도 다시 조회")
    s.set_defaults(func=cmd_sync)

    for name, fn in (("report", cmd_report), ("notify", cmd_notify)):
        c = sub.add_parser(name)
        c.add_argument("kind", nargs="?", default="morning",
                       choices=["morning", "evening", "weekly"])
        c.add_argument("--refresh", action="store_true", help="먼저 sync 한다")
        c.add_argument("--full", action="store_true")
        c.set_defaults(func=fn)

    i = sub.add_parser("items")
    i.add_argument("course", nargs="?", help="vault stem 또는 Canvas ID")
    i.add_argument("--refresh", action="store_true")
    i.set_defaults(func=cmd_items)
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
