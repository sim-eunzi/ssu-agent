# -*- coding: utf-8 -*-
"""urgent — 마감 D-N 이내인데 아직 안 끝난 것.

`study.py due` 는 vault 의 `⬜/✅` 만 본다. 진짜 진도(progress/duration)는
스냅샷에만 있어서, vault 매핑이 실패한 항목(`ambiguous_canvas`)이 영영
`⬜` 로 남아 아침마다 헛알림이 나갔다. 여기는 스냅샷을 직접 본다.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ssu_agent import brief, urgent                             # noqa: E402
from ssu_agent.config import KST                                # noqa: E402

NOW = datetime(2026, 9, 14, 8, 0, tzinfo=KST)


def iso(dt):
    return dt.astimezone(KST).isoformat()


def item(**kw):
    """기본은 '내일 마감 · 미완료 · 25분짜리 영상'."""
    base = {"kind": "lecture", "item_id": kw.pop("item_id", 1), "week": 3,
            "title": "3-1 강의", "content_type": "movie",
            "duration": 1500.0, "progress": 0, "last_at": 0,
            "completed": False, "unopened": False,
            "due_at": iso(NOW + timedelta(days=1)),
            "unlock_at": iso(NOW - timedelta(days=7))}
    base.update(kw)
    return base


def snap(items, weeks=None, stem="선형대수"):
    return {"courses": {"47738": {"stem": stem, "canvas_id": 47738,
                                  "weeks": weeks or {}, "items": items}}}


class Select(unittest.TestCase):
    def test_picks_incomplete_item_inside_window(self):
        got = urgent.select(snap([item()]), NOW, days=2)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["stem"], "선형대수")
        self.assertEqual(got[0]["week"], 3)
        self.assertEqual(got[0]["remaining_sec"], 1500.0)

    def test_completed_is_out_even_below_100_percent(self):
        """출석이 이미 찍힌 98.7% 는 다시 볼 이유가 없다 (4차산업 1주차 1차시 실측)."""
        got = urgent.select(
            snap([item(completed=True, progress=1480.0)]), NOW, days=2)
        self.assertEqual(got, [])

    def test_partial_progress_counts_only_what_is_left(self):
        got = urgent.select(snap([item(progress=900.0)]), NOW, days=2)
        self.assertEqual(got[0]["remaining_sec"], 600.0)

    def test_outside_window_is_out(self):
        far = item(due_at=iso(NOW + timedelta(days=3)))
        self.assertEqual(urgent.select(snap([far]), NOW, days=2), [])

    def test_past_deadline_is_out(self):
        """지난 마감은 여기 일이 아니다 — 재촉이 아니라 '지금 하면 되는 것'만 낸다."""
        old = item(due_at=iso(NOW - timedelta(hours=1)))
        self.assertEqual(urgent.select(snap([old]), NOW, days=2), [])

    def test_not_yet_unlocked_is_out(self):
        locked = item(unlock_at=iso(NOW + timedelta(hours=2)))
        self.assertEqual(urgent.select(snap([locked]), NOW, days=2), [])

    def test_week_deadline_is_the_fallback(self):
        it = item(due_at=None)
        weeks = {"3": {"due_at": iso(NOW + timedelta(days=1)),
                       "unlock_at": iso(NOW - timedelta(days=7))}}
        self.assertEqual(len(urgent.select(snap([it], weeks), NOW, days=2)), 1)

    def test_no_deadline_anywhere_is_out(self):
        self.assertEqual(
            urgent.select(snap([item(due_at=None)]), NOW, days=2), [])

    def test_unopened_length_is_estimated(self):
        """한 번도 안 연 항목은 길이를 모른다. 과목 중앙값으로 세고 밝힌다."""
        known = [item(item_id=i, completed=True, duration=1200.0)
                 for i in range(2, 5)]
        it = item(unopened=True, duration=None, title="3-9 강의")
        got = urgent.select(snap([it] + known), NOW, days=2)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["unopened"])
        self.assertEqual(got[0]["remaining_sec"], 1200.0)

    def test_sorted_by_deadline(self):
        late = item(item_id=1, due_at=iso(NOW + timedelta(days=2)))
        soon = item(item_id=2, due_at=iso(NOW + timedelta(hours=5)))
        got = urgent.select(snap([late, soon]), NOW, days=2)
        self.assertEqual([r["item_id"] for r in got], [2, 1])

    def test_quiz_has_no_progress_but_still_counts(self):
        q = item(kind="quiz", title="3주차 퀴즈", content_type=None,
                 duration=None, progress=None, last_at=None)
        got = urgent.select(snap([q]), NOW, days=2)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "quiz")
        self.assertEqual(got[0]["remaining_sec"], 0.0)


class Fold(unittest.TestCase):
    """vault 가 주차당 1행인 것과 같은 이유로 접는다 — 펴면 아침에 23줄이 나온다."""

    def test_lectures_of_one_week_become_one_row(self):
        rows = urgent.fold(urgent.select(
            snap([item(item_id=1), item(item_id=2, progress=900.0)]),
            NOW, days=2))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["remaining_sec"], 2100.0)

    def test_different_weeks_stay_apart(self):
        rows = urgent.fold(urgent.select(
            snap([item(item_id=1, week=3),
                  item(item_id=2, week=4,
                       unlock_at=iso(NOW - timedelta(days=1)))]),
            NOW, days=2))
        self.assertEqual(len(rows), 2)

    def test_quiz_stays_per_item(self):
        q1 = item(item_id=1, kind="quiz", title="QUIZ 1", duration=None)
        q2 = item(item_id=2, kind="quiz", title="QUIZ 2", duration=None)
        rows = urgent.fold(urgent.select(snap([q1, q2]), NOW, days=2))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["title"] for r in rows], ["QUIZ 1", "QUIZ 2"])

    def test_estimated_marks_the_row(self):
        known = [item(item_id=i, completed=True, duration=1200.0)
                 for i in range(2, 5)]
        rows = urgent.fold(urgent.select(
            snap([item(unopened=True, duration=None)] + known), NOW, days=2))
        self.assertTrue(rows[0]["estimated"])


class Render(unittest.TestCase):
    def test_empty_is_empty_string(self):
        """0바이트 계약 — 호출자가 섹션을 통째로 생략한다 (`study.py due` 와 같다)."""
        self.assertEqual(brief.urgent([], NOW, days=2), "")

    def test_lecture_line_shows_count_and_time_left(self):
        rows = urgent.fold(urgent.select(
            snap([item(item_id=1), item(item_id=2)]), NOW, days=2))
        text = brief.urgent(rows, NOW, days=2)
        self.assertIn("마감 D-2", text)
        self.assertIn("선형대수", text)
        self.assertIn("3주차 강의 2편", text)
        self.assertIn("50분", text)

    def test_quiz_line_says_unsubmitted(self):
        q = item(kind="quiz", title="3주차 퀴즈", duration=None)
        text = brief.urgent(urgent.fold(urgent.select(snap([q]), NOW, days=2)),
                            NOW, days=2)
        self.assertIn("3주차 퀴즈", text)
        self.assertIn("미제출", text)
        self.assertNotIn("남음", text)

    def test_header_counts_items_not_rows(self):
        rows = urgent.fold(urgent.select(
            snap([item(item_id=1), item(item_id=2),
                  item(item_id=3, kind="quiz", title="3주차 퀴즈",
                       duration=None)]), NOW, days=2))
        self.assertIn("3건", brief.urgent(rows, NOW, days=2).splitlines()[0])

    def test_estimated_rows_are_marked(self):
        known = [item(item_id=i, completed=True, duration=1200.0)
                 for i in range(2, 5)]
        rows = urgent.fold(urgent.select(
            snap([item(unopened=True, duration=None)] + known), NOW, days=2))
        self.assertIn("≈", brief.urgent(rows, NOW, days=2))


class Cli(unittest.TestCase):
    """아침 체크인이 붙여넣는 명령이다 — 기본값이 계약이다."""

    def test_command_exists_with_two_day_default(self):
        from ssu_agent import cli
        a = cli.build_parser().parse_args(["urgent"])
        self.assertEqual(a.days, 2)
        self.assertFalse(a.refresh)

    def test_in_overrides_days(self):
        from ssu_agent import cli
        self.assertEqual(
            cli.build_parser().parse_args(["urgent", "--in", "5"]).days, 5)

    def test_prints_nothing_when_empty(self):
        import io
        import contextlib
        from ssu_agent import cli
        args = cli.build_parser().parse_args(["urgent"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_urgent(args, snapshot=snap([item(completed=True)]), now=NOW)
        self.assertEqual(buf.getvalue(), "")

    def test_prints_the_block_when_something_is_left(self):
        import io
        import contextlib
        from ssu_agent import cli
        args = cli.build_parser().parse_args(["urgent"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_urgent(args, snapshot=snap([item()]), now=NOW)
        self.assertIn("마감 D-2", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
