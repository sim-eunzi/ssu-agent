"""summarize.py — 자료 → 마크다운 → LLM 요약 (M3 후반).

의존성도 네트워크도 타지 않는다. 추출과 LLM 호출을 주입한다.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

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


class Chunking(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(len(sm.chunk("짧은 글", 1000)), 1)

    def test_splits_on_paragraph_boundary(self):
        md = "\n\n".join("문단%d %s" % (i, "가" * 300) for i in range(6))
        cs = sm.chunk(md, 700)
        self.assertGreater(len(cs), 1)
        for c in cs:
            self.assertLessEqual(len(c), 900, "경계를 맞추느라 크게 넘치지 않는다")
        self.assertEqual("".join(cs).replace("\n", ""), md.replace("\n", ""),
                         "내용을 잃지 않는다")

    def test_single_huge_paragraph_is_hard_split(self):
        cs = sm.chunk("가" * 2500, 1000)
        self.assertGreaterEqual(len(cs), 3, "문단 경계가 없으면 잘라서라도 나눈다")


class ScannedPdf(unittest.TestCase):
    """실측 — 4차산업혁명과창업 PDF 2개(127·133쪽)가 텍스트 0자였다.
    스캔 이미지라 텍스트 레이어가 없다. 실패가 아니라 '지원 안 함'으로 센다."""

    def test_empty_extraction_is_scanned(self):
        self.assertTrue(sm.looks_scanned("", pages=127))
        self.assertTrue(sm.looks_scanned("   \n\n ", pages=127))

    def test_tiny_extraction_for_many_pages_is_scanned(self):
        self.assertTrue(sm.looks_scanned("쪽번호 1 2 3", pages=100),
                        "100쪽에 12자면 텍스트 레이어가 없는 것이다")

    def test_normal_slides_are_not_scanned(self):
        self.assertFalse(sm.looks_scanned("가" * 2325, pages=13),
                         "실측: 오리엔테이션 13쪽 2325자")
        self.assertFalse(sm.looks_scanned("가" * 2812, pages=35),
                         "실측: 3code 35쪽 2812자")


class Progress(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            sm.save_progress(d, "cid1", {"status": "in_progress", "chunks_done": 2})
            got = sm.load_progress(d, "cid1")
            self.assertEqual(got["chunks_done"], 2)

    def test_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sm.load_progress(wk(tmp), "없음"), {})


class Run(unittest.TestCase):
    def _run(self, tmp, **kw):
        seen = []

        def default_llm(prompt, **k):   # 세는 건 wrapped 가 한다 (이중 계수 금지)
            return "요약본"

        def default_extract(path):
            return ("가" * 2000, {"pages": 13})

        llm = kw.pop("llm", default_llm)
        extract = kw.pop("extract", default_extract)

        def wrapped(prompt, **k):          # 주입한 llm 도 호출을 센다
            seen.append(prompt)
            return llm(prompt, **k)

        with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
            res = sm.run("2026-2", extract=extract, llm=wrapped,
                         log=lambda *a: None, **kw)
        res["_prompts"] = seen
        return res

    def test_writes_markdown_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            res = self._run(tmp)
            self.assertEqual(res["done"], 1)
            self.assertTrue((d / "markdown" / "강의.md").exists(), "원본 마크다운 보존")
            s = (d / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## 강의.pdf", s, "자료별 섹션")
            self.assertIn("요약본", s)

    def test_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            wk(tmp)
            self._run(tmp)
            res = self._run(tmp)
            self.assertEqual((res["done"], res["skipped"]), (0, 1))
            self.assertEqual(res["_prompts"], [], "이미 된 건 LLM 을 부르지 않는다")

    def test_summary_section_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            self._run(tmp)
            self._run(tmp)
            s = (d / "summary.md").read_text(encoding="utf-8")
            self.assertEqual(s.count("## 강의.pdf"), 1)

    def test_scanned_pdf_is_recorded_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            res = self._run(tmp, extract=lambda p: ("", {"pages": 127}))
            self.assertEqual(res["unsupported"], 1)
            self.assertEqual(res["failed"], 0, "스캔 PDF 는 실패가 아니다")
            self.assertEqual(sm.load_progress(d, "cid1")["status"], "unsupported_scanned")
            self.assertEqual(res["_prompts"], [], "LLM 을 부르지 않는다")

    def test_llm_error_is_recorded_and_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)

            def boom(prompt, **k):
                raise RuntimeError("429 rate limit")
            res = self._run(tmp, llm=boom)
            self.assertEqual(res["failed"], 1)
            pr = sm.load_progress(d, "cid1")
            self.assertEqual(pr["status"], "failed")
            self.assertIn("429", pr["last_error"])
            self.assertIn("updated_at", pr, "언제 실패했는지 남는다")
            # 다음 실행에서 다시 시도한다
            res2 = self._run(tmp)
            self.assertEqual(res2["done"], 1)

    def test_call_budget_stops_and_resumes_next_run(self):
        """🔴 상한에 걸려도 거기까지가 장부에 남아 다음 실행이 이어받는다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            long_md = "\n\n".join("문단%d %s" % (i, "가" * 400) for i in range(8))

            def ex(p):
                return (long_md, {"pages": 40})
            res = self._run(tmp, extract=ex, chunk_size=500, max_calls=2)
            self.assertEqual(res["budget_hit"], True)
            pr = sm.load_progress(d, "cid1")
            self.assertEqual(pr["status"], "in_progress")
            self.assertEqual(len(pr["partials"]), 2, "한 청크당 부분요약 하나")
            self.assertGreater(pr["chunks_total"], 2)

            res2 = self._run(tmp, extract=ex, chunk_size=500, max_calls=99)
            self.assertEqual(res2["done"], 1)
            pr2 = sm.load_progress(d, "cid1")
            self.assertEqual(pr2["status"], "done")
            self.assertLess(len(res2["_prompts"]), pr2["chunks_total"] + 1,
                            "이미 한 청크는 다시 부르지 않는다")

    def test_markdown_is_reused_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            calls = []

            def ex(p):
                calls.append(p)
                return ("\n\n".join("문단%d %s" % (i, "가"*400) for i in range(6)),
                        {"pages": 40})
            self._run(tmp, extract=ex, chunk_size=500, max_calls=1)
            self._run(tmp, extract=ex, chunk_size=500, max_calls=99)
            self.assertEqual(len(calls), 1, "PDF 를 다시 파싱하지 않는다")


class Estimate(unittest.TestCase):
    """키 없이 비용을 먼저 본다."""

    def test_counts_without_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            wk(tmp)
            with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
                e = sm.estimate("2026-2", extract=lambda p: ("가" * 2000, {"pages": 13}))
            self.assertEqual(e["docs"], 1)
            self.assertEqual(e["chars"], 2000)
            self.assertGreater(e["est_input_tokens"], 0)
            self.assertGreater(e["est_usd"], 0)

    def test_scanned_excluded_from_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            wk(tmp)
            with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
                e = sm.estimate("2026-2", extract=lambda p: ("", {"pages": 127}))
            self.assertEqual(e["docs"], 0)
            self.assertEqual(e["unsupported"], 1)


if __name__ == "__main__":
    unittest.main()
