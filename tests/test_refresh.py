# -*- coding: utf-8 -*-
"""refresh — 한 번에 다 돌리기.

코코봇이 "최신 LMS 업데이트해줘" 한 마디로 부르는 입구다. 스킬은 로직이 0 이어야
하므로(univ-save 규약) 순서·중단·보고를 전부 여기가 진다.
"""
import contextlib
import io
import unittest

from ssu_agent import refresh as rf


def ok(name, line, **kw):
    d = {"ok": True, "line": line}
    d.update(kw)
    return lambda: d


def boom(name):
    def f():
        raise RuntimeError(name + " 터짐")
    return f


class Order(unittest.TestCase):
    def test_runs_all_four_in_order(self):
        seen = []

        def rec(n, line):
            def f():
                seen.append(n)
                return {"ok": True, "line": line}
            return f

        res = rf.run({k: rec(k, k + " 됨") for k in rf.STEPS})
        self.assertEqual(seen, list(rf.STEPS))
        self.assertFalse(res["aborted"])
        self.assertEqual(len(res["steps"]), 4)


class SyncIsTheGate(unittest.TestCase):
    """🔴 cron 래퍼가 지키던 규칙을 코드로 옮긴다 —
    sync 가 실패하면 **낡은 스냅샷으로 vault 를 고치지 않는다.**"""

    def test_sync_failure_aborts_everything_after(self):
        seen = []
        fns = {"sync": boom("sync")}
        for k in rf.STEPS[1:]:
            fns[k] = (lambda n: lambda: (seen.append(n), {"ok": True, "line": ""})[1])(k)
        res = rf.run(fns)
        self.assertEqual(seen, [], "sync 가 죽었는데 뒤 단계가 돌면 안 된다")
        self.assertTrue(res["aborted"])
        self.assertIn("sync", res["steps"][0]["name"])
        self.assertFalse(res["steps"][0]["ok"])

    def test_later_failure_does_not_abort_the_rest(self):
        """자료 다운로드가 하나 실패했다고 요약까지 막을 이유는 없다."""
        seen = []
        fns = {"sync": ok("sync", "수집 완료"),
               "vault": ok("vault", "적용 3"),
               "materials": boom("materials"),
               "summary": (lambda: (seen.append("summary"),
                                    {"ok": True, "line": "요약 1"})[1])}
        res = rf.run(fns)
        self.assertEqual(seen, ["summary"])
        self.assertFalse(res["aborted"])
        self.assertFalse(res["steps"][2]["ok"])
        self.assertIn("materials 터짐", res["steps"][2]["line"])


class Selection(unittest.TestCase):
    def test_can_skip_the_step_that_costs_money(self):
        seen = []
        fns = {k: (lambda n: lambda: (seen.append(n),
                                      {"ok": True, "line": ""})[1])(k)
               for k in rf.STEPS}
        rf.run(fns, want=("sync", "vault", "materials"))
        self.assertNotIn("summary", seen, "--no-summary 면 LLM 을 안 부른다")

    def test_unknown_step_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            rf.run({}, want=("sync", "없는단계"))


class Report(unittest.TestCase):
    """코코봇이 그대로 붙여넣는 텍스트다. 마크업은 안 붙인다 (README 규약)."""

    def test_render_is_one_line_per_step(self):
        res = rf.run({"sync": ok("sync", "7과목 237항목"),
                      "vault": ok("vault", "적용 3 · 스킵 0"),
                      "materials": ok("materials", "저장 2 · 12.4MB"),
                      "summary": ok("summary", "요약 2 · $0.03")})
        txt = rf.render(res)
        self.assertIn("7과목 237항목", txt)
        self.assertIn("요약 2 · $0.03", txt)
        self.assertEqual(len(txt.strip().splitlines()), 5, "제목 1 + 단계 4")

    def test_failed_step_is_marked_not_hidden(self):
        res = rf.run({"sync": ok("sync", "됨"), "vault": boom("vault"),
                      "materials": ok("materials", "됨"),
                      "summary": ok("summary", "됨")})
        txt = rf.render(res)
        self.assertIn("⚠️", txt)
        self.assertIn("vault 터짐", txt)

    def test_abort_says_why_and_what_was_not_done(self):
        res = rf.run({"sync": boom("sync")})
        txt = rf.render(res)
        self.assertIn("멈췄다", txt)
        self.assertIn("낡은", txt, "왜 멈췄는지가 보고에 있어야 한다")


if __name__ == "__main__":
    unittest.main()


class QuietCapturesTheRightLine(unittest.TestCase):
    """🔴 실행해보고서야 드러난 것 — 마지막 줄을 긁으면 틀린다.

    cmd_materials 는 요약 뒤에 미공개 자료 목록을 더 찍는다. 그래서
    '③ 자료' 칸에 `선형대수 W01 강의자료ppt.zip` 이 올라왔다.
    """

    def test_summary_line_wins_over_trailing_detail(self):
        from ssu_agent import cli

        def fake(_a):
            cli._sum("저장 2 · 12.4MB")
            print("아직 공개 전 14건")
            print("    선형대수 W01 강의자료ppt.zip")
        got = cli._quiet(fake, None)
        self.assertEqual(got["line"], "저장 2 · 12.4MB")
        self.assertIn("강의자료ppt.zip", got["detail"], "상세는 버리지 않는다")

    def test_falls_back_to_last_line_when_nothing_marked(self):
        from ssu_agent import cli
        got = cli._quiet(lambda _a: print("표시 안 한 명령"), None)
        self.assertEqual(got["line"], "표시 안 한 명령")

    def test_marker_does_not_leak_between_calls(self):
        from ssu_agent import cli
        cli._quiet(lambda _a: cli._sum("첫번째"), None)
        got = cli._quiet(lambda _a: cli._sum("두번째"), None)
        self.assertEqual(got["line"], "두번째")


class DryRunMustNotSpendMoney(unittest.TestCase):
    """🔴 실행해보고서야 드러난 것 — `--dry-run` 이 진짜 LLM 경로를 탔다.

    오늘은 요약이 전부 끝나 있어서 호출 0회로 지나갔을 뿐이다.
    새 자료가 있는 날이었으면 "확인만 할게" 가 돈을 썼다.
    """

    def test_dry_run_routes_summary_to_estimate(self):
        from ssu_agent import cli
        seen = {}

        keep = (cli.sync.run, cli.cmd_summarize,
                cli.cmd_vault_sync, cli.cmd_materials)
        cli.sync.run = lambda **kw: {"courses": {"1": {"items": []}}}
        cli.cmd_vault_sync = cli.cmd_materials = lambda a: None
        cli.cmd_summarize = lambda a: seen.setdefault("estimate", a.estimate)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                cli.cmd_refresh(cli._Args(dry_run=True, no_summary=False,
                                          no_materials=False, limit=None,
                                          verbose=False))
                dry = seen.pop("estimate")
                cli.cmd_refresh(cli._Args(dry_run=False, no_summary=False,
                                          no_materials=False, limit=None,
                                          verbose=False))
        finally:
            (cli.sync.run, cli.cmd_summarize,
             cli.cmd_vault_sync, cli.cmd_materials) = keep
        self.assertTrue(dry, "--dry-run 이면 estimate 로 가야 한다")
        self.assertFalse(seen["estimate"], "실행이면 진짜 요약을 한다")


class ErrorsAreTranslated(unittest.TestCase):
    """봇이 그대로 은지에게 보내는 문장이다. 스택 조각을 보내면 안 된다.
    실측 — 토큰이 죽으면 URL 과 JSON 이 통째로 나왔다."""

    def test_401_says_the_token_expired(self):
        got = rf.explain("HttpError: HTTP 401 https://canvas.ssu.ac.kr/api/v1/"
                         'courses?x=1 {"errors":[{"message":"Invalid access token."}]}')
        self.assertIn("토큰", got)
        self.assertNotIn("http", got.lower(), "URL 을 은지에게 보내지 않는다")

    def test_network_error_says_network(self):
        self.assertIn("연결", rf.explain("URLError: <urlopen error timed out>"))

    def test_unknown_error_is_passed_through_not_swallowed(self):
        self.assertEqual(rf.explain("ValueError: 이상한 것"), "ValueError: 이상한 것")

    def test_render_translates_too(self):
        res = rf.run({"sync": lambda: {"ok": False, "line": "HttpError: HTTP 401 x"}})
        self.assertIn("토큰", rf.render(res))


class LimitReachesSummarize(unittest.TestCase):
    """🔴 2026-09-05 실측 — `refresh` 의 요약 단계가 TypeError 로 죽었다.

    `--limit` 기본값이 `summarize` 파서는 MAX_CALLS(30) 인데 `refresh` 파서만
    None 이었다. 그 None 이 `_Args` → `cmd_summarize` → `run(max_calls=None)`
    까지 그대로 흘러 `summarize.py` 의 `res["calls"] >= max_calls` 에서 터진다.

    09-02 부터 있던 결함인데 안 드러난 이유: 그 비교는 **아직 요약 안 된 대상이
    하나라도 있어야** 도달한다. 그전엔 대상 5개가 전부 done 이라 루프가 전부
    continue 로 빠졌다 (cron 로그 4일치가 "건너뜀 5 · 호출 0회"). 오늘 자료
    19개가 새로 들어오면서 처음 닿았다.

    🔴 기존 테스트가 못 잡은 이유 — 파서를 안 거치고 `_Args(limit=None)` 를
    손으로 만들어 넣었다. 그래서 **파서 기본값을 통과시키는 것**이 이 테스트의 일이다.
    """

    def test_refresh_parser_gives_summarize_a_usable_limit(self):
        from ssu_agent import cli, summarize

        a = cli.build_parser().parse_args(["refresh"])
        self.assertIsNotNone(
            a.limit, "refresh 의 --limit 기본값이 None 이면 요약 단계가 죽는다")

        # 실제 비교 지점까지 닿는지 — 여기서 TypeError 가 나면 회귀다.
        try:
            0 >= a.limit
        except TypeError as e:
            self.fail("summarize.run 의 상한 비교가 터진다: %s" % e)
        self.assertEqual(a.limit, summarize.MAX_CALLS,
                         "두 파서의 --limit 기본값은 같아야 한다")
