# -*- coding: utf-8 -*-
"""출석 수집의 두 결함에 대한 회귀 테스트 (2026-09-05 실측).

1) 모듈 `external_url` 의 id 는 **권위가 아니다.** 과목이 복제되면 옛 id 가
   남아 `attendance_items/{id}` 가 404 를 준다. 실측: 확장현실 15/15,
   정치철학 53/60, 4차산업 48/52, 한반도 35/37 이 이 경우였고 전부
   '안 봤다'로 오해됐다. 권위는 `attendance_items/summary` 다.
2) 퀴즈·과제는 `completed` 를 **조회조차 안 했다** — 항상 미완료였다.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ssu_agent import sync


class TestLectureRecords(unittest.TestCase):
    def test_summary_is_authoritative(self):
        """모듈에 없는 summary id 도 아이템이 된다 (확장현실 = 겹침 0)."""
        recs = sync.lecture_records([901767, 916981], [
            {"item_id": 640796, "week": 1, "title": "확장현실디자인 1주차",
             "module": "1주차", "position": 2},
        ])
        self.assertEqual({r["item_id"] for r in recs}, {901767, 916981})
        self.assertTrue(all(r["kind"] == "lecture" for r in recs))

    def test_stale_module_id_is_dropped(self):
        """summary 에 없는 모듈 id 는 버린다 — 404 를 부르러 가지 않는다."""
        recs = sync.lecture_records([901767], [{"item_id": 640796, "week": 1}])
        self.assertNotIn(640796, {r["item_id"] for r in recs})

    def test_matching_id_keeps_module_metadata(self):
        """id 가 맞는 과목(선형대수·3code)은 기존 동작 그대로."""
        recs = sync.lecture_records([906532], [
            {"item_id": 906532, "week": 1, "title": "선대_1_1",
             "module": "1주차", "position": 1, "html_url": "u"},
        ])
        self.assertEqual(recs[0]["title"], "선대_1_1")
        self.assertEqual(recs[0]["week"], 1)
        self.assertEqual(recs[0]["html_url"], "u")


class TestWeekFromLearningX(unittest.TestCase):
    def test_lx_week_fills_missing_week(self):
        it = {"item_id": 901767, "week": None, "lx_week": 1,
              "lx_title": "확장현실디자인 1주차"}
        sync.backfill_from_lx(it, {1: "1주차"})
        self.assertEqual(it["week"], 1)
        self.assertEqual(it["title"], "확장현실디자인 1주차")
        self.assertEqual(it["module"], "1주차")

    def test_canvas_week_wins_when_present(self):
        it = {"item_id": 1, "week": 3, "lx_week": 9, "title": "t"}
        sync.backfill_from_lx(it, {})
        self.assertEqual(it["week"], 3)
        self.assertEqual(it["title"], "t")


class TestWeekFromTitle(unittest.TestCase):
    """LearningX 도 Canvas 도 주차를 안 주는 자료가 있다 (4차산업 11~14주차
    학습자료 4건, 실측). 주차가 없으면 `materials` 가 통째로 건너뛴다."""

    def test_material_code_gives_week(self):
        self.assertEqual(
            sync.week_from_title("[학습자료]_4차산업혁명과 창업_1101_v1.0"), 11)
        self.assertEqual(
            sync.week_from_title("[학습자료]_4차산업혁명과 창업_0101_v1.0"), 1)

    def test_explicit_week_wins(self):
        self.assertEqual(sync.week_from_title("3주차 강의 _9901_"), 3)

    def test_out_of_range_is_ignored(self):
        self.assertIsNone(sync.week_from_title("자료_9901_v1.0"))

    def test_no_signal(self):
        self.assertIsNone(sync.week_from_title("확장현실디자인"))
        self.assertIsNone(sync.week_from_title(None))

    def test_backfill_uses_title_last(self):
        it = {"item_id": 1, "week": None, "lx_week": None,
              "title": "[학습자료]_4차산업혁명과 창업_1201_v1.0"}
        sync.backfill_from_lx(it, {})
        self.assertEqual(it["week"], 12)


class TestGradedCompletion(unittest.TestCase):
    def test_submitted_assignment_is_done(self):
        idx = sync.completion_index(
            [{"id": 735431, "quiz_id": None}],
            [{"assignment_id": 735431, "workflow_state": "submitted",
              "submitted_at": "2026-09-05T04:40:25Z"}], {})
        self.assertTrue(idx[("assignment", 735431)])

    def test_unsubmitted_is_not_done(self):
        idx = sync.completion_index(
            [{"id": 735419, "quiz_id": 47131}],
            [{"assignment_id": 735419, "workflow_state": "unsubmitted",
              "submitted_at": None}], {})
        self.assertFalse(idx[("quiz", 47131)])

    def test_practice_quiz_without_assignment(self):
        """1주차 퀴즈는 practice_quiz 라 assignment 가 없다 — 실측."""
        idx = sync.completion_index([], [], {
            47138: [{"workflow_state": "complete", "finished_at":
                     "2026-09-05T04:40:25Z", "score": 4.0}]})
        self.assertTrue(idx[("quiz", 47138)])

    def test_untouched_quiz_returns_empty_list(self):
        idx = sync.completion_index([], [], {47131: []})
        self.assertFalse(idx.get(("quiz", 47131), False))

    def test_discussion_maps_through_assignment(self):
        idx = sync.completion_index(
            [{"id": 900, "discussion_topic": {"id": 55}}],
            [{"assignment_id": 900, "workflow_state": "graded",
              "submitted_at": "2026-09-01T00:00:00Z"}], {})
        self.assertTrue(idx[("discussion", 55)])


if __name__ == "__main__":
    unittest.main()


class TestProgressScale(unittest.TestCase):
    """`progress` 는 **초**다 — 2026-09-05 실측: 확장현실 1주차
    progress=2307.42 · duration=2307.43 · last_at=2307.43.

    퍼센트로 읽으면 `dur * 23.07` 이 되어 부분 시청 영상이 전부
    '남은 시간 0'(=다 봤다)으로 오판된다. 완료 항목은 단락되어 안 드러났다.
    """
    def _rs(self, **kw):
        from ssu_agent import risk
        base = {"content_type": "movie", "completed": False, "unopened": False}
        base.update(kw)
        return risk.remaining_seconds(base)

    def test_progress_is_seconds_not_percent(self):
        # 1800초짜리를 900초 봤다 → 900초 남는다 (퍼센트로 읽으면 0)
        self.assertAlmostEqual(self._rs(duration=1800, progress=900, last_at=0),
                               900.0, places=1)

    def test_full_progress_leaves_nothing(self):
        self.assertAlmostEqual(self._rs(duration=2307.43, progress=2307.42,
                                        last_at=2307.43), 0.0, places=1)

    def test_last_at_still_a_floor(self):
        self.assertAlmostEqual(self._rs(duration=1800, progress=0, last_at=600),
                               1200.0, places=1)

    def test_progress_never_exceeds_duration(self):
        self.assertGreaterEqual(self._rs(duration=100, progress=999, last_at=0), 0.0)
