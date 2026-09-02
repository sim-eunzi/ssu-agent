"""study_cli.py — vault 반영 어댑터 (M2).

실제 state/ 를 건드리지 않는다 — skipped 저장소는 항상 주입한다.

네트워크도 study.py 도 타지 않는다. 스냅샷은 딕셔너리 리터럴,
study.py 호출은 주입한 가짜 러너로 대신한다.
"""

import json
import unittest

from ssu_agent import study_cli as sc


def kst_due(mmdd):
    """그 날 23:59 KST 마감을 UTC ISO 로. 손으로 9시간 빼다 틀리지 않게."""
    return "2026-%sT14:59:00Z" % mmdd


def snap(items, weeks=None):
    return {"canvas_id": 1, "stem": "선형대수", "mode": "offline",
            "weeks": weeks or {}, "items": items}


def lec(week, title="강의", due=None, completed=False, unopened=False):
    return {"kind": "lecture", "week": week, "title": title,
            "due_at": due, "completed": completed, "unopened": unopened}


def quiz(week, title="3주차 퀴즈", due=None):
    return {"kind": "quiz", "week": week, "title": title, "due_at": due}


def vrows(*rows):
    """study.py list --json 의 rows 형태."""
    out = []
    for wk, typ, due, done, item in rows:
        out.append({"week": str(wk), "date": "-", "item": item, "type": typ,
                    "due": due, "done": done, "note": "-"})
    return {"course": "선형대수", "file": "선형대수", "rows": out}


def runner(payload, rc=0, err=""):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return rc, json.dumps(payload, ensure_ascii=False), err
    run.calls = calls
    return run


class KindMapping(unittest.TestCase):
    def test_canvas_kind_to_vault_type(self):
        e = snap([quiz(3), lec(3), {"kind": "assignment", "week": 4,
                                    "title": "과제", "due_at": None},
                  {"kind": "discussion", "week": 5, "title": "토론",
                   "due_at": None}])
        got = sc.canvas_rows(e)
        self.assertEqual(got[(3, "퀴즈")]["title"], "3주차 퀴즈")
        self.assertIn((3, "강의"), got)
        self.assertIn((4, "과제"), got)
        self.assertIn((5, "과제"), got, "토론도 vault 에선 과제다 (seed_univ 선례)")

    def test_week_none_is_dropped(self):
        self.assertEqual(sc.canvas_rows(snap([quiz(None)])), {})

    def test_due_at_to_mmdd_kst(self):
        e = snap([quiz(3, due=kst_due("09-21"))])
        self.assertEqual(sc.canvas_rows(e)[(3, "퀴즈")]["due"], "09-21")


class LectureAggregation(unittest.TestCase):
    """vault 는 주차당 강의 1행(강의계획서 단위), Canvas 는 영상 N개.
    한나아렌트 6개/주차 · 3code 7개/주차 · 한반도평화와통일 전 주차 중복 (실측)."""

    W = {"3": {"due_at": kst_due("09-21"), "late_at": kst_due("09-28")}}

    def test_many_items_collapse_to_one_row(self):
        got = sc.canvas_rows(snap([lec(3, "3-1"), lec(3, "3-2"), lec(3, "3-3")],
                                  weeks=self.W))
        self.assertEqual(list(got), [(3, "강의")])
        self.assertFalse(got[(3, "강의")]["ambiguous"], "집계 대상이지 모호한 게 아니다")

    def test_all_watched_means_done(self):
        got = sc.canvas_rows(snap([lec(3, "a", completed=True),
                                   lec(3, "b", completed=True)], weeks=self.W))
        self.assertTrue(got[(3, "강의")]["completed"])

    def test_one_unwatched_is_not_done(self):
        got = sc.canvas_rows(snap([lec(3, "a", completed=True),
                                   lec(3, "b", completed=False)], weeks=self.W))
        self.assertFalse(got[(3, "강의")]["completed"], "하나라도 남으면 그 주차는 안 끝났다")

    def test_due_comes_from_week_metadata(self):
        """미개봉 항목은 due_at 이 아예 없다 (404 라 attendance 를 못 받는다).
        주차 마감은 lessons 가 준다 — 7과목 15주 100% 커버 (핸드오프 §2.2)."""
        got = sc.canvas_rows(snap([lec(3, "a"), lec(3, "b")], weeks=self.W))
        self.assertEqual(got[(3, "강의")]["due"], "09-21")

    def test_falls_back_to_latest_item_due(self):
        got = sc.canvas_rows(snap([lec(3, "a", due=kst_due("09-19")),
                                   lec(3, "b", due=kst_due("09-21"))]))
        self.assertEqual(got[(3, "강의")]["due"], "09-21", "가장 늦은 것 — 이르면 오알림")

    def test_week_without_lecture_items_makes_no_row(self):
        got = sc.canvas_rows(snap([quiz(3)], weeks={"3": {"due_at": kst_due("09-21")},
                                                    "4": {"due_at": kst_due("09-28")}}))
        self.assertNotIn((3, "강의"), got)
        self.assertNotIn((4, "강의"), got)

    def test_aggregated_row_is_named_by_week(self):
        got = sc.canvas_rows(snap([lec(3, "3-1 어쩌고"), lec(3, "3-2 저쩌고")],
                                  weeks=self.W))
        self.assertEqual(got[(3, "강의")]["title"], "3주차 강의",
                         "vault 의 'N주차 퀴즈' 명명과 맞춘다")

    def test_quizzes_still_ambiguous_when_duplicated(self):
        """퀴즈는 접지 않는다 — vault 행이 여럿이어야 할 수도 있어 추측이 위험하다."""
        got = sc.canvas_rows(snap([quiz(8, "A"), quiz(8, "B")]))
        self.assertTrue(got[(8, "퀴즈")]["ambiguous"])


class Plan(unittest.TestCase):
    def _plan(self, vault, canvas_items):
        return sc.plan("선형대수", sc.parse_vault(vault),
                       sc.canvas_rows(snap(canvas_items)))

    def test_deadline_moved_earlier(self):
        acts, _ = self._plan(vrows((3, "퀴즈", "09-28", False, "3주차 퀴즈")),
                             [quiz(3, due=kst_due("09-21"))])
        self.assertEqual([a["action"] for a in acts], ["set_due"])
        self.assertEqual(acts[0]["due"], "09-21")

    def test_deadline_moved_later(self):
        """🔴 연장이 앞당김보다 흔하다. vault 가 옛 마감을 들면 오알림이 뜬다."""
        acts, _ = self._plan(vrows((3, "퀴즈", "09-21", False, "3주차 퀴즈")),
                             [quiz(3, due=kst_due("09-28"))])
        self.assertEqual([a["action"] for a in acts], ["set_due"])
        self.assertEqual(acts[0]["due"], "09-28")

    def test_same_deadline_is_noop(self):
        acts, _ = self._plan(vrows((3, "퀴즈", "09-21", False, "3주차 퀴즈")),
                             [quiz(3, due=kst_due("09-21"))])
        self.assertEqual(acts, [], "변경이 없으면 쓰지 않는다 (커밋 노이즈)")

    def test_done_row_keeps_its_deadline(self):
        acts, _ = self._plan(vrows((3, "퀴즈", "09-21", True, "3주차 퀴즈")),
                             [quiz(3, due=kst_due("09-28"))])
        self.assertEqual(acts, [], "✅ 행은 알림에서 이미 빠진다 — 기록을 흐리지 않는다")

    def test_completed_marks_done(self):
        acts, _ = self._plan(vrows((3, "강의", "09-21", False, "행렬")),
                             [lec(3, due=kst_due("09-21"), completed=True)])
        self.assertEqual([a["action"] for a in acts], ["done"])

    def test_never_undoes_a_check(self):
        """🔴 사람이 코코봇으로 체크한 걸 기계가 지우면 안 된다."""
        acts, _ = self._plan(vrows((3, "강의", "09-21", True, "행렬")),
                             [lec(3, due=kst_due("09-21"), completed=False)])
        self.assertEqual(acts, [])

    def test_unopened_is_not_completion(self):
        """404 는 '안 봤다' 지 '했다' 가 아니다."""
        acts, _ = self._plan(vrows((3, "강의", "09-21", False, "행렬")),
                             [lec(3, due=kst_due("09-21"),
                                  completed=False, unopened=True)])
        self.assertEqual(acts, [])

    def test_vault_only_row_is_untouched(self):
        """K-MOOC · 손으로 넣은 것 · 계획서에서 온 미래 항목이 섞여 있다."""
        acts, _ = self._plan(vrows((9, "시험", "-", False, "중간고사")), [])
        self.assertEqual(acts, [], "rm 을 쓰지 않는다")

    def test_canvas_only_row_is_added(self):
        """그 주차가 vault 에 이미 있으면, 새 유형은 추가한다."""
        acts, _ = self._plan(vrows((6, "강의", "10-12", False, "6주차 강의")),
                             [quiz(6, title="6주차 퀴즈", due=kst_due("10-12"))])
        self.assertEqual([a["action"] for a in acts], ["add"])
        self.assertEqual(acts[0]["type"], "퀴즈")
        self.assertEqual(acts[0]["due"], "10-12")

    def test_week_absent_from_vault_is_skipped_not_added(self):
        """🔴 add 는 locate() 를 안 타서 exit 2 가 안 난다 — 없는 주차도 행을 만든다.

        정치철학-한나아렌트는 vault 주차가 1~14 + 16 이다 (15주차 없음).
        Canvas 15주차를 add 하면 '없는 개념의 행'이 생긴다. 이건 새 항목이
        아니라 주차 매핑 실패이므로, 만들지 말고 기록만 한다.
        """
        v = vrows((14, "강의", "12-07", False, "지혜로운 정치판단"),
                  (16, "시험", "-", False, "기말고사"))
        acts, skips = self._plan(v, [quiz(15, title="15주차 퀴즈",
                                          due=kst_due("12-14"))])
        self.assertEqual(acts, [])
        self.assertEqual(skips[0]["reason"], "week_not_in_vault")

    def test_never_writes_note(self):
        acts, _ = self._plan(
            vrows((3, "퀴즈", "09-28", False, "3주차 퀴즈")),
            [quiz(3, due=kst_due("09-21"))])
        for a in acts:
            self.assertNotIn("note", a, "비고는 사람 것이다 (195행 중 71행이 채워져 있다)")

    def test_ambiguous_key_is_skipped_not_guessed(self):
        v = vrows((3, "퀴즈", "09-21", False, "3주차 퀴즈"),
                  (3, "퀴즈", "09-22", False, "3주차 보충 퀴즈"))
        acts, skips = self._plan(v, [quiz(3, due=kst_due("09-28"))])
        self.assertEqual(acts, [])
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["reason"], "ambiguous")

    def test_duplicate_canvas_key_is_skipped(self):
        """한 주차에 같은 유형이 둘이면 어느 vault 행에 붙일지 알 수 없다."""
        acts, skips = self._plan(vrows((3, "퀴즈", "09-21", False, "3주차 퀴즈")),
                                 [quiz(3, title="A", due=kst_due("09-28")),
                                  quiz(3, title="B", due=kst_due("09-28"))])
        self.assertEqual(acts, [])
        self.assertEqual(skips[0]["reason"], "ambiguous_canvas")

    def test_unsafe_title_is_skipped(self):
        """sane() 이 | 와 개행을 거부한다. 우회하면 대시보드가 행을 조용히 버린다."""
        acts, skips = self._plan(vrows((6, "강의", "10-12", False, "6주차 강의")),
                                 [quiz(6, title="a | b")])
        self.assertEqual(acts, [])
        self.assertEqual(skips[0]["reason"], "unsafe_title")


class VaultRead(unittest.TestCase):
    def test_uses_list_json(self):
        run = runner(vrows((3, "퀴즈", "09-21", False, "3주차 퀴즈")))
        rows = sc.read_vault("선형대수", run=run)
        self.assertIn((3, "퀴즈"), rows)
        self.assertIn("--json", run.calls[0])
        self.assertIn("list", run.calls[0])


class Apply(unittest.TestCase):
    def test_dry_run_passes_flag_and_changes_nothing(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return 0, "", ""
        acts = [{"action": "set_due", "stem": "선형대수", "week": 3,
                 "type": "퀴즈", "due": "09-28"}]
        sc.apply(acts, run=run, dry_run=True, skipped={})
        self.assertIn("--dry-run", calls[0])

    def test_lock_timeout_is_passed(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            return 0, "", ""
        sc.apply([{"action": "done", "stem": "선형대수", "week": 3,
                   "type": "강의"}], run=run, skipped={})
        self.assertIn("--lock-timeout", calls[0])

    def test_exit3_aborts_the_cycle(self):
        """락 대기 초과 — 다음 폴링에서 재시도한다."""
        def run(args, **kw):
            return 3, "", "다른 study.py 가 돌고 있다"
        res = sc.apply([{"action": "done", "stem": "선형대수", "week": 3,
                         "type": "강의"},
                        {"action": "done", "stem": "선형대수", "week": 4,
                         "type": "강의"}], run=run, skipped={})
        self.assertTrue(res["locked"])
        self.assertEqual(res["applied"], 0)

    def test_exit2_is_recorded_and_not_repeated(self):
        def run(args, **kw):
            return 2, "", "해당 행이 없다"
        act = {"action": "set_due", "stem": "선형대수", "week": 16,
               "type": "시험", "due": "12-16"}
        store = {}
        r1 = sc.apply([act], run=run, skipped=store)
        r2 = sc.apply([act], run=run, skipped=store)
        self.assertEqual(len(store), 1, "같은 항목을 반복 기록하지 않는다")
        self.assertEqual(r2["skipped"], 0, "이미 기록된 건 다시 시도하지 않는다")
        self.assertEqual(r1["skipped"], 1)


class Skips(unittest.TestCase):
    def test_plan_skips_are_recorded_once(self):
        store = {}
        s = [{"stem": "선형대수", "week": 3, "type": "퀴즈", "reason": "ambiguous"}]
        self.assertEqual(sc.note_skips(s, store), 1)
        self.assertEqual(sc.note_skips(s, store), 0, "같은 항목을 반복 기록하지 않는다")
        self.assertEqual(len(store), 1)

    def test_different_reasons_are_separate(self):
        store = {}
        sc.note_skips([{"stem": "a", "week": 1, "type": "퀴즈",
                        "reason": "ambiguous"}], store)
        sc.note_skips([{"stem": "a", "week": 1, "type": "퀴즈",
                        "reason": "unsafe_title"}], store)
        self.assertEqual(len(store), 2)


if __name__ == "__main__":
    unittest.main()
