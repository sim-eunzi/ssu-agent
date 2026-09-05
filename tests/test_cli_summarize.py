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
