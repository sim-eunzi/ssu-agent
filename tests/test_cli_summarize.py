# -*- coding: utf-8 -*-
"""CLI — sources 플래그 · status 명령 · 잠금 · _Args 필드 누락 방어.

🔴 `_Args` 자리에서 두 번 밟았다. 2026-09-05 `--limit=None` 사고가 이 껍데기의
조용한 통과에서 났다. 필드를 빠뜨리면 **생성 시점에** 터지게 한다.
"""
import unittest

from ssu_agent import cli


class ArgsShell(unittest.TestCase):
    def test_missing_field_raises(self):
        with self.assertRaises(TypeError):
            cli._Args(cli.REFRESH_STEP_FIELDS, refresh=False, dry_run=True)

    def test_complete_set_ok(self):
        a = cli._Args(cli.REFRESH_STEP_FIELDS,
                      refresh=False, dry_run=True, verbose=False)
        self.assertTrue(a.dry_run)

    def test_without_fields_stays_permissive(self):
        self.assertFalse(cli._Args(models=False).models)


class Parser(unittest.TestCase):
    def test_summarize_defaults_off(self):
        a = cli.build_parser().parse_args(["summarize"])
        self.assertFalse(a.include_manual)
        self.assertFalse(a.manual_only)

    def test_manual_only(self):
        a = cli.build_parser().parse_args(["summarize", "--manual-only"])
        self.assertTrue(a.manual_only)

    def test_flags_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(
                ["summarize", "--include-manual", "--manual-only"])

    def test_status_command_exists(self):
        a = cli.build_parser().parse_args(["status"])
        self.assertFalse(a.json)


class Sources(unittest.TestCase):
    def test_translation(self):
        f = cli._sources
        self.assertEqual(f(cli._Args(include_manual=False, manual_only=False)),
                         ("ledger",))
        self.assertEqual(f(cli._Args(include_manual=True, manual_only=False)),
                         ("ledger", "manual"))
        self.assertEqual(f(cli._Args(include_manual=False, manual_only=True)),
                         ("manual",))


class SourcesReachSummarize(unittest.TestCase):
    """🔴 `cli._sources(a)` 를 번역만 하고 안 넘기면 조용히 통과한다 — 예전에
    `summarize.run(...)`/`summarize.estimate(...)` 에서 `sources=_sources(a)` 가
    지워졌을 때도 전체 스위트가 통과했다. 실제로 넘어가는 튜플을 스파이로 잡는다."""

    def _spy_run(self):
        calls = []
        orig = cli.summarize.run

        def fake(*a, **k):
            calls.append(k)
            return {"done": 0, "skipped": 0, "unsupported": 0, "failed": 0,
                    "calls": 0, "budget_hit": False, "in_tokens": 0, "out_tokens": 0}
        cli.summarize.run = fake
        return calls, orig

    def _spy_estimate(self):
        calls = []
        orig = cli.summarize.estimate

        def fake(*a, **k):
            calls.append(k)
            return {"docs": 0, "chars": 0, "chunks": 0, "unsupported": 0,
                    "skipped": 0, "est_input_tokens": 0, "est_output_tokens": 0,
                    "est_usd": 0.0}
        cli.summarize.estimate = fake
        return calls, orig

    def _args(self, **kw):
        base = dict(models=False, estimate=False, limit=5,
                    include_manual=False, manual_only=False)
        base.update(kw)
        return cli._Args(**base)

    def test_run_default_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_run()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args())
        finally:
            cli.summarize.run = orig
        self.assertEqual(calls[0].get("sources"), ("ledger",))

    def test_run_include_manual_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_run()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args(include_manual=True))
        finally:
            cli.summarize.run = orig
        self.assertEqual(calls[0].get("sources"), ("ledger", "manual"))

    def test_run_manual_only_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_run()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args(manual_only=True))
        finally:
            cli.summarize.run = orig
        self.assertEqual(calls[0].get("sources"), ("manual",))

    def test_estimate_default_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_estimate()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args(estimate=True))
        finally:
            cli.summarize.estimate = orig
        self.assertEqual(calls[0].get("sources"), ("ledger",))

    def test_estimate_include_manual_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_estimate()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args(estimate=True, include_manual=True))
        finally:
            cli.summarize.estimate = orig
        self.assertEqual(calls[0].get("sources"), ("ledger", "manual"))

    def test_estimate_manual_only_sources(self):
        import contextlib
        import io
        calls, orig = self._spy_estimate()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(self._args(estimate=True, manual_only=True))
        finally:
            cli.summarize.estimate = orig
        self.assertEqual(calls[0].get("sources"), ("manual",))


class StatusDispatch(unittest.TestCase):
    """cmd_status 가 실제로 collect/render 를 부르는지, --json 이 render 를
    건너뛰고 collect 를 그대로 찍는지 — 여태 아무 테스트도 없었다."""

    def test_plain_dispatches_collect_and_render(self):
        import contextlib
        import io
        seen = {"collect": [], "render": []}
        orig_collect, orig_render = cli.status.collect, cli.status.render
        orig_get = cli.get

        def fake_collect(sem, root=None):
            seen["collect"].append(sem)
            return {"semester": sem}

        def fake_render(st):
            seen["render"].append(st)
            return "렌더된 현황\n"

        cli.status.collect = fake_collect
        cli.status.render = fake_render
        cli.get = lambda: cli._Args(semester="2026-2")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_status(cli._Args(json=False))
        finally:
            cli.status.collect, cli.status.render = orig_collect, orig_render
            cli.get = orig_get
        self.assertEqual(rc, 0)
        self.assertEqual(seen["collect"], ["2026-2"])
        self.assertEqual(seen["render"], [{"semester": "2026-2"}])
        self.assertIn("렌더된 현황", buf.getvalue())

    def test_json_dispatches_collect_only(self):
        import contextlib
        import io
        import json as jsonlib
        seen = {"collect": [], "render": []}
        orig_collect, orig_render = cli.status.collect, cli.status.render
        orig_get = cli.get

        def fake_collect(sem, root=None):
            seen["collect"].append(sem)
            return {"semester": sem, "ledger": {"done": 1}}

        def fake_render(st):
            seen["render"].append(st)
            return "안 불려야 한다\n"

        cli.status.collect = fake_collect
        cli.status.render = fake_render
        cli.get = lambda: cli._Args(semester="2026-2")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_status(cli._Args(json=True))
        finally:
            cli.status.collect, cli.status.render = orig_collect, orig_render
            cli.get = orig_get
        self.assertEqual(rc, 0)
        self.assertEqual(seen["collect"], ["2026-2"])
        self.assertEqual(seen["render"], [], "--json 은 render 를 부르지 않는다")
        out = jsonlib.loads(buf.getvalue())
        self.assertEqual(out, {"semester": "2026-2", "ledger": {"done": 1}})


class Locking(unittest.TestCase):
    def test_busy_lock_exits_zero_without_running(self):
        """잠금 실패는 에러가 아니다 — 크론 로그가 매일 빨개지면 안 된다."""
        import contextlib
        import io

        called = []
        orig_run, orig_held = cli.summarize.run, cli.lock.held
        cli.summarize.run = lambda *a, **k: called.append(1)

        @contextlib.contextmanager
        def busy(name, root=None):
            yield False

        cli.lock.held = busy
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_summarize(cli._Args(
                    models=False, estimate=False, limit=5,
                    include_manual=False, manual_only=False))
        finally:
            cli.summarize.run, cli.lock.held = orig_run, orig_held
        self.assertEqual(rc, 0)
        self.assertEqual(called, [], "이미 돌고 있으면 실행하지 않는다")
        self.assertIn("이미", buf.getvalue())

    def test_estimate_is_not_locked(self):
        """읽기만 하는 명령을 잠그면 '현황 좀 보려다 막히는' 일이 생긴다."""
        import contextlib
        import io

        seen = []
        orig_est, orig_held = cli.summarize.estimate, cli.lock.held
        cli.summarize.estimate = lambda sem, **k: {
            "docs": 0, "chars": 0, "chunks": 0, "unsupported": 0, "skipped": 0,
            "est_input_tokens": 0, "est_output_tokens": 0, "est_usd": 0.0}

        @contextlib.contextmanager
        def spy(name, root=None):
            seen.append(name)
            yield True

        cli.lock.held = spy
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(cli._Args(
                    models=False, estimate=True, limit=5,
                    include_manual=False, manual_only=False))
        finally:
            cli.summarize.estimate, cli.lock.held = orig_est, orig_held
        self.assertEqual(seen, [], "estimate 는 잠그지 않는다")


class RefreshHasNoSummary(unittest.TestCase):
    def test_parser_dropped_summary_flags(self):
        p = cli.build_parser()
        for bad in (["refresh", "--no-summary"], ["refresh", "--limit", "5"]):
            with self.assertRaises(SystemExit):
                p.parse_args(bad)

    def test_reports_pending_line(self):
        """🔴 fns 를 부르지 않는다 — 부르면 진짜 sync 가 돌아 네트워크를 탄다."""
        import contextlib
        import io

        orig_run, orig_collect, orig_get = (
            cli.refresh.run, cli.status.collect, cli.get)
        cli.refresh.run = lambda fns, want=(): {"steps": [], "aborted": False}
        cli.get = lambda: cli._Args(semester="2026-2")
        cli.status.collect = lambda sem, root=None: {
            "semester": sem,
            "ledger": {"done": 0, "pending": 5, "failed": 0, "unsupported": 0},
            "manual": {"done": 0, "pending": 2, "failed": 0, "unsupported": 0},
            "video": None, "last_progress_at": None}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli.cmd_refresh(cli._Args(no_materials=True, dry_run=True,
                                          verbose=False))
        finally:
            cli.refresh.run, cli.status.collect, cli.get = (
                orig_run, orig_collect, orig_get)
        out = buf.getvalue()
        self.assertIn("미요약", out)
        self.assertIn("5", out)
        self.assertIn("2", out)


if __name__ == "__main__":
    unittest.main()
