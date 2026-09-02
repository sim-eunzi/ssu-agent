"""M1 회귀 테스트. 외부 의존성·네트워크 없음.

    python3 -m unittest discover -s tests -t .
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ssu_agent import brief, events, net, risk, sync            # noqa: E402
from ssu_agent.config import KST, get                          # noqa: E402

SAMPLES = os.path.join(ROOT, "docs", "samples")


def fixture(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


class TestFormParse(unittest.TestCase):
    """§2.1 함정 1 — 정규식이 아니라 태그 단위로 파싱해야 한다."""

    def test_id_between_name_and_value(self):
        html = ('<form action="https://lx/launch" method="POST">'
                '<input type="hidden" name="oauth_nonce" id="n" value="abc123">'
                '<input id="x" type="hidden" value="WRONG" name="oauth_signature">'
                '<input name="empty">'
                '</form>')
        action, fields = net.parse_lti_form(html)
        self.assertEqual(action, "https://lx/launch")
        self.assertEqual(fields["oauth_nonce"], "abc123")
        self.assertEqual(fields["oauth_signature"], "WRONG")
        self.assertEqual(fields["empty"], "")

    def test_entities_unescaped(self):
        _, f = net.parse_lti_form(
            '<form action="/a"><input name="u" value="a&amp;b=c"></form>')
        self.assertEqual(f["u"], "a&b=c")

    def test_no_form(self):
        with self.assertRaises(ValueError):
            net.parse_lti_form("<html>로그인이 필요합니다</html>")


class TestNextLink(unittest.TestCase):
    def test_next(self):
        h = {"Link": '<https://c/1>; rel="current",<https://c/2>; rel="next"'}
        self.assertEqual(net.next_link(h), "https://c/2")

    def test_no_next(self):
        self.assertIsNone(net.next_link({"Link": '<https://c/9>; rel="last"'}))
        self.assertIsNone(net.next_link({}))


class FakeCanvas:
    def __init__(self, mods):
        self._mods = mods

    def modules(self, cid):
        return self._mods


class TestModuleScan(unittest.TestCase):
    def test_week_parse(self):
        self.assertEqual(sync.week_of("12주차"), 12)
        self.assertEqual(sync.week_of(" 3 주 차 "), 3)
        self.assertIsNone(sync.week_of("오리엔테이션"))

    def test_board_tool_excluded(self):
        """47737 은 tool 73(출석) 60개 + tool 41(Q&A) 15개가 섞여 있다."""
        lec, graded, weeks = sync.scan_modules(
            FakeCanvas(fixture("modules_detail_47737.json")), 47737)
        self.assertEqual(len(lec), 60)
        self.assertEqual(len(weeks), 15)
        self.assertTrue(all(l["item_id"] for l in lec))
        kinds = sorted({g["kind"] for g in graded})
        self.assertEqual(kinds, ["assignment", "quiz"])

    def test_graded_due_from_canvas(self):
        """퀴즈/과제 마감은 JWT 없이 모듈 API 로 확보된다."""
        _, graded, _ = sync.scan_modules(
            FakeCanvas(fixture("modules_detail_47737.json")), 47737)
        q = [g for g in graded if g["title"] == "1주차 퀴즈"][0]
        self.assertEqual(q["due_at"], "2026-09-14T14:59:00Z")
        self.assertEqual(q["week"], 1)

    def test_empty_course_is_not_an_error(self):
        """48130 은 학기 초라 모듈만 있고 아이템이 없다."""
        lec, graded, weeks = sync.scan_modules(
            FakeCanvas(fixture("modules_detail_48130.json")), 48130)
        self.assertEqual((len(lec), len(graded)), (0, 0))
        self.assertEqual(len(weeks), 15)

    def test_all_seven_courses_parse(self):
        for cid in (47737, 47738, 47762, 48130, 48462, 48466, 49791):
            lec, _, weeks = sync.scan_modules(
                FakeCanvas(fixture("modules_detail_{}.json".format(cid))), cid)
            self.assertEqual(len(weeks), 15, cid)


class TestLessons(unittest.TestCase):
    def test_week_position_wins(self):
        parsed = sync.parse_lessons([
            {"week_position": 1, "due_at": "2026-09-14T14:59:59Z", "title": "1주차"},
            {"week_position": 2, "due_at": "2026-09-21T14:59:59Z", "title": "2주차"},
        ])
        self.assertEqual(parsed[2]["due_at"], "2026-09-21T14:59:59Z")

    def test_index_fallback(self):
        parsed = sync.parse_lessons([{"title": "a"}, {"title": "b"}])
        self.assertEqual(sorted(parsed), [1, 2])

    def test_garbage(self):
        self.assertEqual(sync.parse_lessons(None), {})
        self.assertEqual(sync.parse_lessons({"error": 500}), {})


class TestRemaining(unittest.TestCase):
    def test_completed_is_zero(self):
        self.assertEqual(risk.remaining_seconds(
            {"completed": True, "duration": 900, "content_type": "movie"}), 0)

    def test_untouched(self):
        self.assertAlmostEqual(risk.remaining_seconds(
            {"duration": 778.9, "progress": 0, "last_at": 0,
             "content_type": "movie"}), 778.9)

    def test_pdf_has_no_duration(self):
        self.assertEqual(risk.remaining_seconds(
            {"duration": None, "content_type": "pdf"}), 0)

    def test_last_at_is_the_floor(self):
        """progress 스케일이 애매해도 last_at 아래로는 내려가지 않는다."""
        self.assertAlmostEqual(risk.remaining_seconds(
            {"duration": 1000, "progress": 0, "last_at": 600,
             "content_type": "movie"}), 400)

    def test_percent_and_fraction_agree_at_50(self):
        a = risk.remaining_seconds({"duration": 1000, "progress": 50,
                                    "content_type": "movie"})
        b = risk.remaining_seconds({"duration": 1000, "progress": 0.5,
                                    "content_type": "movie"})
        self.assertAlmostEqual(a, b)


class TestUnopened(unittest.TestCase):
    """attendance_items 는 '내가 연 항목'만 있는 개인 기록 — 404 는 정보다."""

    def test_unopened_counts_as_full(self):
        self.assertEqual(risk.remaining_seconds(
            {"unopened": True, "title": "3주차 1차시"}, 1400), 1400)

    def test_unopened_material_is_not_video_time(self):
        for t in ("[학습자료]_4차산업혁명과 창업_0101", "3주차 강의자료", "교안 PDF"):
            self.assertEqual(risk.remaining_seconds({"unopened": True, "title": t}, 1400), 0, t)

    def test_unopened_falls_back_to_default(self):
        self.assertEqual(risk.remaining_seconds({"unopened": True, "title": "x"}),
                         risk.DEFAULT_DURATION)

    def test_everlec_is_video(self):
        self.assertEqual(risk.remaining_seconds(
            {"content_type": "everlec", "duration": 603}), 603)

    def test_pdf_is_not_video(self):
        self.assertEqual(risk.remaining_seconds(
            {"content_type": "pdf", "duration": 900}), 0)

    def test_estimate_needs_a_sample(self):
        one = [{"content_type": "movie", "duration": 1596}]
        self.assertIsNone(risk.estimate_duration(one))
        self.assertEqual(risk.estimate_duration(one, min_sample=1), 1596)
        three = [{"content_type": "movie", "duration": d} for d in (900, 1200, 1800)]
        self.assertEqual(risk.estimate_duration(three), 1200)

    def test_assess_marks_estimated(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=KST)
        snap = {"courses": {"48466": {
            "canvas_id": 48466, "stem": "확장현실디자인",
            "weeks": {"1": {"due_at": "2026-09-14T14:59:59Z"}},
            "items": [{"kind": "lecture", "item_id": 640796, "week": 1,
                       "title": "확장현실디자인 1주차", "unopened": True}]}}}
        a = risk.assess(snap, now=now)
        c = a["courses"][0]
        self.assertTrue(c["estimated"])
        self.assertEqual(c["unopened_count"], 1)
        self.assertEqual(a["total"]["unopened_count"], 1)
        self.assertGreater(c["remaining_hours"], 0)


class TestAvailability(unittest.TestCase):
    def setUp(self):
        self.cfg = get()

    def test_full_weekday(self):
        s = datetime(2026, 9, 7, 0, 0, tzinfo=KST)      # 월
        e = datetime(2026, 9, 8, 0, 0, tzinfo=KST)
        self.assertAlmostEqual(risk.available_hours(s, e, self.cfg), 1.5, places=2)

    def test_full_weekend_day(self):
        s = datetime(2026, 9, 5, 0, 0, tzinfo=KST)      # 토
        e = datetime(2026, 9, 6, 0, 0, tzinfo=KST)
        self.assertAlmostEqual(risk.available_hours(s, e, self.cfg), 4.0, places=2)

    def test_past_deadline_is_zero(self):
        s = datetime(2026, 9, 7, 12, 0, tzinfo=KST)
        self.assertEqual(risk.available_hours(s, s - timedelta(hours=1), self.cfg), 0)
        self.assertEqual(risk.available_hours(s, None, self.cfg), 0)

    def test_before_window_gives_full_day(self):
        """평일 오전에 봐도 그날 저녁분은 아직 남아 있다."""
        s = datetime(2026, 9, 7, 9, 0, tzinfo=KST)
        e = datetime(2026, 9, 7, 23, 59, 59, tzinfo=KST)
        self.assertAlmostEqual(risk.available_hours(s, e, self.cfg), 1.5, places=2)

    def test_after_window_gives_nothing_today(self):
        s = datetime(2026, 9, 7, 23, 59, 59, tzinfo=KST)
        e = datetime(2026, 9, 8, 0, 30, tzinfo=KST)
        self.assertAlmostEqual(risk.available_hours(s, e, self.cfg), 0.0, places=2)


class TestAssess(unittest.TestCase):
    def snap(self, items, weeks=None):
        return {"courses": {"47738": {
            "canvas_id": 47738, "stem": "4차산업혁명과창업",
            "weeks": weeks or {}, "items": items}}}

    def test_week_deadline_fallback(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=KST)
        a = risk.assess(self.snap(
            [{"kind": "lecture", "item_id": 1, "week": 2, "title": "2주차 1차시",
              "duration": 3600, "content_type": "movie", "completed": False}],
            weeks={"2": {"due_at": "2026-09-13T14:59:59Z"}}), now=now)
        c = a["courses"][0]
        self.assertEqual(c["pending_count"], 1)
        self.assertTrue(c["nearest_deadline"].startswith("2026-09-13"))

    def test_locked_item_ignored(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=KST)
        a = risk.assess(self.snap(
            [{"kind": "lecture", "item_id": 1, "week": 9, "duration": 3600,
              "content_type": "movie", "completed": False,
              "unlock_at": "2026-10-25T15:00:00Z",
              "due_at": "2026-11-01T14:59:59Z"}]), now=now)
        self.assertEqual(a["courses"][0]["pending_count"], 0)

    def test_overdue_separated(self):
        now = datetime(2026, 9, 20, 12, 0, tzinfo=KST)
        a = risk.assess(self.snap(
            [{"kind": "lecture", "item_id": 1, "week": 1, "title": "지난 것",
              "duration": 600, "content_type": "movie", "completed": False,
              "due_at": "2026-09-14T14:59:59Z"}]), now=now)
        self.assertEqual(len(a["overdue"]), 1)
        self.assertEqual(a["courses"][0]["pending_count"], 0)

    def test_impossible_load_is_red(self):
        """토요일 밤, 일요일 마감에 6시간이 남으면 불가여야 한다."""
        now = datetime(2026, 9, 5, 21, 0, tzinfo=KST)
        items = [{"kind": "lecture", "item_id": i, "week": 1, "title": "x",
                  "duration": 3600, "content_type": "movie", "completed": False,
                  "due_at": "2026-09-06T14:59:59Z"} for i in range(6)]
        a = risk.assess(self.snap(items), now=now)
        self.assertEqual(a["courses"][0]["level"], "불가")
        self.assertGreaterEqual(a["courses"][0]["ratio"], 1.0)

    def test_light_load_is_green(self):
        now = datetime(2026, 9, 5, 10, 0, tzinfo=KST)
        a = risk.assess(self.snap(
            [{"kind": "lecture", "item_id": 1, "week": 1, "title": "x",
              "duration": 600, "content_type": "movie", "completed": False,
              "due_at": "2026-09-13T14:59:59Z"}]), now=now)
        self.assertEqual(a["courses"][0]["level"], "여유")


class TestEvents(unittest.TestCase):
    def snap(self, due, ann=None, extra=None):
        items = [{"kind": "lecture", "item_id": 1, "title": "1주차 1차시",
                  "week": 1, "due_at": due, "html_url": "u"}]
        if extra:
            items.append(extra)
        return {"courses": {"47738": {"stem": "4차산업혁명과창업", "weeks": {},
                                      "items": items,
                                      "announcements": ann or []}}}

    def test_first_run_is_silent(self):
        """첫 실행에 40건씩 쏘면 안 된다."""
        self.assertEqual(events.detect(self.snap("2026-09-14T14:59:59Z"), {}), [])

    def test_deadline_moved_earlier(self):
        got = events.detect(self.snap("2026-09-14T14:59:59Z"),
                            self.snap("2026-09-20T14:59:59Z"))
        self.assertEqual([e["type"] for e in got], ["deadline_moved"])

    def test_deadline_pushed_later_is_not_an_event(self):
        got = events.detect(self.snap("2026-09-25T14:59:59Z"),
                            self.snap("2026-09-20T14:59:59Z"))
        self.assertEqual(got, [])

    def test_new_soon_only_within_window(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=KST)
        near = {"kind": "quiz", "content_id": 9, "title": "퀴즈", "week": 2,
                "due_at": "2026-09-08T14:59:59Z", "html_url": "q"}
        far = dict(near, content_id=10, due_at="2026-10-08T14:59:59Z")
        base = self.snap("2026-09-14T14:59:59Z")
        self.assertEqual(
            [e["type"] for e in events.detect(self.snap("2026-09-14T14:59:59Z", extra=near),
                                              base, now=now)], ["new_soon"])
        self.assertEqual(
            events.detect(self.snap("2026-09-14T14:59:59Z", extra=far), base, now=now), [])

    def test_notice_keywords(self):
        base = self.snap("2026-09-14T14:59:59Z", ann=[])
        hit = self.snap("2026-09-14T14:59:59Z",
                        ann=[{"id": 5, "title": "9/15 휴강 안내", "text": "", "html_url": "a"}])
        miss = self.snap("2026-09-14T14:59:59Z",
                         ann=[{"id": 6, "title": "환영합니다", "text": "안녕", "html_url": "a"}])
        self.assertEqual([e["type"] for e in events.detect(hit, base)], ["notice"])
        self.assertEqual(events.detect(miss, base), [])

    def test_dedupe_key_is_stable(self):
        got = events.detect(self.snap("2026-09-14T14:59:59Z"),
                            self.snap("2026-09-20T14:59:59Z"))
        again = events.detect(self.snap("2026-09-14T14:59:59Z"),
                              self.snap("2026-09-20T14:59:59Z"))
        self.assertEqual(got[0]["key"], again[0]["key"])
        self.assertEqual(events.unseen(got, {got[0]["key"]: "ts"}), [])


class TestBrief(unittest.TestCase):
    def build(self):
        now = datetime(2026, 9, 7, 7, 30, tzinfo=KST)
        snap = {"courses": {"47738": {
            "canvas_id": 47738, "stem": "4차산업혁명과창업", "weeks": {},
            "items": [{"kind": "lecture", "item_id": 1, "week": 2,
                       "title": "2주차 <1차시> & 실습", "duration": 5400,
                       "content_type": "movie", "completed": False,
                       "due_at": "2026-09-08T14:59:59Z",
                       "html_url": "https://canvas.ssu.ac.kr/x?a=1&b=2"}]}}}
        return risk.assess(snap, now=now)

    def test_estimate_note_appears(self):
        a = self.build()
        a["total"]["unopened_count"] = 156
        self.assertIn("156개", brief.render(a, "morning"))

    def test_all_three_render(self):
        a = self.build()
        for kind in ("morning", "evening", "weekly"):
            self.assertIn("4차산업혁명과창업", brief.render(a, kind))

    def test_no_markup_and_no_escaping(self):
        """마크업은 보내는 쪽이 정한다. 붙이지도, 이스케이프하지도 않는다.

        HTML 로 보낼지 Markdown 으로 보낼지 모르는 채로 이스케이프하면
        받는 쪽이 되돌려야 한다. 제목은 원문 그대로 통과시킨다.
        """
        text = brief.render(self.build(), "evening")
        self.assertIn("2주차 <1차시> & 실습", text)     # 원문 그대로
        self.assertNotIn("&amp;", text)                # 이스케이프 안 함
        self.assertNotIn("<a ", text)                  # 링크 태그 안 붙임
        self.assertNotIn("<b>", text)

    def test_events_appear_in_text(self):
        ev = [{"type": "deadline_moved", "stem": "선형대수", "title": "2주차",
               "from": "2026-09-20T14:59:59Z", "to": "2026-09-14T14:59:59Z"}]
        text = brief.render(self.build(), "morning", ev)
        self.assertIn("마감 앞당겨짐", text)
        self.assertIn("선형대수", text)

    def test_payload_shape(self):
        p = brief.payload(self.build(), "weekly", [], {"semester": "2026-2"})
        for k in ("generated_at", "kind", "text", "total", "courses",
                  "overdue", "events"):
            self.assertIn(k, p)
        self.assertEqual(p["kind"], "weekly")
        self.assertEqual(p["semester"], "2026-2")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DotenvDuplicates(unittest.TestCase):
    """🔴 2026-09-02 에 두 번 걸린 함정 — 같은 키가 두 줄이면 어느 쪽이 이기나.

    setdefault 로 한 줄씩 넣으면 **먼저 나온 줄**이 이긴다. 빈 자리표시자를
    위에 두고 진짜 값을 아래에 append 하면 빈 값이 이겨서 인증이 실패한다.
    파일 안에서는 **나중 줄**이 이겨야 append 가 직관대로 동작한다.
    """

    def _load(self, text, env):
        import tempfile, os, pathlib
        from ssu_agent import config as cfg
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / ".env"
            p.write_text(text, encoding="utf-8")
            keep = dict(os.environ)
            os.environ.clear()
            os.environ.update(env)
            try:
                cfg.load_dotenv(p)
                return dict(os.environ)
            finally:
                os.environ.clear()
                os.environ.update(keep)

    def test_later_line_wins(self):
        got = self._load("K=\nK=진짜값\n", {})
        self.assertEqual(got.get("K"), "진짜값", "나중에 append 한 줄이 이긴다")

    def test_real_environment_still_wins_over_file(self):
        got = self._load("K=파일값\n", {"K": "환경값"})
        self.assertEqual(got.get("K"), "환경값", "이미 있는 환경변수는 안 덮는다")
