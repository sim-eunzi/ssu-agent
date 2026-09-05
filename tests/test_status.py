# -*- coding: utf-8 -*-
"""status — 장부를 세기만 한다. 네트워크도 LLM 도 안 탄다.

🔴 세는 근거는 반드시 `summarize._targets` 다. status 가 materials/ 를 자기
방식으로 훑으면, 화면이 "2건"이라 하고 실행이 3건을 요약하는 어긋남이 난다.
"""
import json
import pathlib
import tempfile
import unittest

from ssu_agent import status as st
from ssu_agent import summarize as sm


def wk(tmp, course="선형대수", week=3):
    d = pathlib.Path(tmp) / "2026-2" / course / ("W%02d" % week)
    (d / "materials").mkdir(parents=True, exist_ok=True)
    (d / "materials" / "강의.pdf").write_bytes(b"%PDF-1.7")
    (d / "meta.json").write_text(json.dumps({
        "course": course, "week": week,
        "items": {"cid1": {"file": "강의.pdf", "size": 8, "sha256": "x"}},
    }, ensure_ascii=False), encoding="utf-8")
    return d


class Collect(unittest.TestCase):
    def test_empty_semester(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = st.collect("2026-2", root=pathlib.Path(tmp))
            self.assertEqual(got["ledger"],
                             {"done": 0, "pending": 0, "failed": 0, "unsupported": 0})
            self.assertIsNone(got["last_progress_at"])

    def test_counts_ledger_and_manual_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            (d / "materials" / "손1.pdf").write_bytes(b"%PDF-1.7")
            (d / "materials" / "손2.pdf").write_bytes(b"%PDF-1.7")
            sm.save_progress(d, "manual-손1.pdf", {"file": "손1.pdf", "status": "done"})
            got = st.collect("2026-2", root=pathlib.Path(tmp))
            self.assertEqual(got["ledger"]["pending"], 1)
            self.assertEqual(got["manual"], {"done": 1, "pending": 1,
                                             "failed": 0, "unsupported": 0})

    def test_status_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            for i, s in enumerate(("done", "failed", "unsupported_scanned",
                                   "in_progress")):
                name = "손%d.pdf" % i
                (d / "materials" / name).write_bytes(b"%PDF-1.7")
                sm.save_progress(d, "manual-" + name, {"file": name, "status": s})
            m = st.collect("2026-2", root=pathlib.Path(tmp))["manual"]
            self.assertEqual(m["done"], 1)
            self.assertEqual(m["failed"], 1)
            self.assertEqual(m["unsupported"], 1)
            self.assertEqual(m["pending"], 1,
                             "in_progress 는 '남은 것' 이다 — 다음 실행이 이어받는다")

    def test_non_pdf_manual_is_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            (d / "materials" / "교재.pptx").write_bytes(b"PK\x03\x04")
            m = st.collect("2026-2", root=pathlib.Path(tmp))["manual"]
            self.assertEqual(sum(m.values()), 0, "셀 수 있는 것만 센다")

    def test_last_progress_at_is_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            sm.save_progress(d, "cid1", {"file": "강의.pdf", "status": "done"})
            got = st.collect("2026-2", root=pathlib.Path(tmp))
            self.assertIsNotNone(got["last_progress_at"])

    def test_broken_meta_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            (d / "meta.json").write_text("{깨짐", encoding="utf-8")
            got = st.collect("2026-2", root=pathlib.Path(tmp))
            self.assertEqual(got["ledger"]["pending"], 0)
            self.assertEqual(got["manual"]["pending"], 1, "손 업로드는 살아남는다")


class Render(unittest.TestCase):
    def _st(self, **kw):
        base = {"semester": "2026-2",
                "ledger": {"done": 3, "pending": 5, "failed": 0, "unsupported": 2},
                "manual": {"done": 1, "pending": 2, "failed": 0, "unsupported": 0},
                "video": None, "last_progress_at": "2026-09-05 03:14"}
        base.update(kw)
        return base

    def test_no_video_line_when_none(self):
        out = st.render(self._st())
        self.assertNotIn("동영상", out, "Phase 4 전에는 0 으로 채운 거짓 줄을 안 낸다")
        self.assertIn("자동 수집", out)
        self.assertIn("직접 올림", out)

    def test_no_markup(self):
        out = st.render(self._st())
        for ch in ("*", "_", "`", "<"):
            self.assertNotIn(ch, out, "HTML 이냐 Markdown 이냐는 보내는 쪽이 정한다")

    def test_pending_line_counts_both(self):
        line = st.pending_line(self._st())
        self.assertIn("5", line)
        self.assertIn("2", line)

    def test_pending_line_when_nothing_left(self):
        line = st.pending_line(self._st(
            ledger={"done": 3, "pending": 0, "failed": 0, "unsupported": 2},
            manual={"done": 1, "pending": 0, "failed": 0, "unsupported": 0}))
        self.assertIn("미요약 없음", line)


if __name__ == "__main__":
    unittest.main()
