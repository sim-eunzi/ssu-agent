# -*- coding: utf-8 -*-
"""동영상 전사 — 순수 함수 (Phase 1 · T1).

계획: docs/superpowers/plans/2026-09-05-video-transcription-phase1.md
스펙: docs/superpowers/specs/2026-09-05-video-transcription-design.md
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ssu_agent import transcribe

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "docs", "samples")


def _xml():
    with open(os.path.join(SAMPLES, "commons_content_video.xml"),
              encoding="utf-8") as f:
        return f.read()


class TestParseMediaUri(unittest.TestCase):
    def test_picks_progressive_cdn(self):
        """flash_fallback(pseudo) 이 아니라 html5 progressive 를 잡는다."""
        got = transcribe.parse_media_uri(_xml())
        self.assertEqual(got, "https://ssuin-object.commonscdn.com/ssu-contents"
                              "/contents31/ssu1000001/68b27aeb26a32/contents"
                              "/media_files/mobile/ssmovie.mp4")

    def test_no_media_uri(self):
        self.assertIsNone(transcribe.parse_media_uri("<content/>"))

    def test_garbage(self):
        self.assertIsNone(transcribe.parse_media_uri(""))
        self.assertIsNone(transcribe.parse_media_uri(None))


class TestPlan(unittest.TestCase):
    def _snap(self, **over):
        item = {"kind": "lecture", "content_type": "movie", "unopened": False,
                "week": 1, "title": "1-2 한나 아렌트", "duration": 3463.98,
                "item_id": 906000,
                "view_url": "https://commons.ssu.ac.kr/em/68b27aeb26a32"}
        item.update(over)
        return {"courses": {"47737": {"stem": "정치철학-한나아렌트",
                                      "items": [item]}}}

    def test_picks_movie(self):
        got = transcribe.plan(self._snap())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["content_id"], "68b27aeb26a32")
        self.assertEqual(got[0]["stem"], "정치철학-한나아렌트")
        self.assertEqual(got[0]["week"], 1)

    def test_everlec_counts_too(self):
        self.assertEqual(len(transcribe.plan(self._snap(content_type="everlec"))), 1)

    def test_pdf_excluded(self):
        self.assertEqual(transcribe.plan(self._snap(content_type="pdf")), [])

    def test_unopened_excluded(self):
        self.assertEqual(transcribe.plan(self._snap(unopened=True)), [])

    def test_no_week_excluded(self):
        """주차를 모르면 어디에 둘지 모른다."""
        self.assertEqual(transcribe.plan(self._snap(week=None)), [])

    def test_no_view_url_excluded(self):
        self.assertEqual(transcribe.plan(self._snap(view_url=None)), [])


class TestSafeStem(unittest.TestCase):
    def test_strips_separators(self):
        got = transcribe.safe_stem("1-2/한나 아렌트", "abc")
        self.assertNotIn("/", got)

    def test_falls_back_to_content_id(self):
        self.assertEqual(transcribe.safe_stem("", "abc123"), "abc123")
        self.assertEqual(transcribe.safe_stem(None, "abc123"), "abc123")


class TestToMarkdown(unittest.TestCase):
    def test_header_and_timestamps(self):
        segs = [{"start": 0.0, "text": " 안녕하세요"},
                {"start": 75.5, "text": "오늘은 아렌트를 봅니다"}]
        md = transcribe.to_markdown(segs, {
            "source": "https://commons.ssu.ac.kr/em/abc",
            "duration": 3463.98, "model": "small", "at": "2026-09-05"})
        self.assertIn("commons.ssu.ac.kr/em/abc", md)
        self.assertIn("57분", md)          # 3463.98초 → 57분
        self.assertIn("small", md)
        self.assertIn("[00:00] 안녕하세요", md)
        self.assertIn("[01:15] 오늘은 아렌트를 봅니다", md)

    def test_empty_segments(self):
        md = transcribe.to_markdown([], {"source": "s", "duration": 0,
                                         "model": "small", "at": "2026-09-05"})
        self.assertIn("s", md)


if __name__ == "__main__":
    unittest.main()
