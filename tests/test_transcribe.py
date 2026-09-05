# -*- coding: utf-8 -*-
"""동영상 전사 — 순수 함수 (Phase 1 · T1).

계획: docs/superpowers/plans/2026-09-05-video-transcription-phase1.md
스펙: docs/superpowers/specs/2026-09-05-video-transcription-design.md
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

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


class _FakeResp:
    """`open_url` 이 돌려주는 것의 최소 형태 — status·headers·read·컨텍스트."""

    def __init__(self, body, status=200, headers=None):
        self._b = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, n):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownload(unittest.TestCase):
    def test_fresh_download(self):
        seen = {}

        def open_url(url, headers):
            seen["headers"] = headers
            return _FakeResp(b"abcdef")

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "v.mp4"
            n = transcribe.download("http://x/v.mp4", dest, open_url=open_url)
            self.assertEqual(dest.read_bytes(), b"abcdef")
        self.assertEqual(n, 6)
        self.assertNotIn("Range", seen["headers"])   # 처음이면 Range 안 붙인다

    def test_resumes_from_part(self):
        """이미 받아둔 만큼은 다시 받지 않는다."""
        seen = {}

        def open_url(url, headers):
            seen["headers"] = headers
            return _FakeResp(b"def", status=206)

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "v.mp4"
            Path(str(dest) + ".part").write_bytes(b"abc")
            n = transcribe.download("http://x/v.mp4", dest, open_url=open_url)
            self.assertEqual(dest.read_bytes(), b"abcdef")
        self.assertEqual(seen["headers"]["Range"], "bytes=3-")
        self.assertEqual(n, 6)

    def test_part_is_renamed_on_success(self):
        def open_url(url, headers):
            return _FakeResp(b"xy")

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "v.mp4"
            transcribe.download("http://x/v.mp4", dest, open_url=open_url)
            self.assertTrue(dest.exists())
            self.assertFalse(Path(str(dest) + ".part").exists())

    def test_server_ignores_range_restarts(self):
        """206 이 아니라 200 이 오면 서버가 Range 를 무시한 것 —
        이어붙이면 파일이 깨지므로 처음부터 다시 쓴다."""
        def open_url(url, headers):
            return _FakeResp(b"ABCDEF", status=200)

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "v.mp4"
            Path(str(dest) + ".part").write_bytes(b"abc")
            transcribe.download("http://x/v.mp4", dest, open_url=open_url)
            self.assertEqual(dest.read_bytes(), b"ABCDEF")

    def test_creates_missing_directory(self):
        def open_url(url, headers):
            return _FakeResp(b"z")

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "nested" / "deep" / "v.mp4"
            transcribe.download("http://x/v.mp4", dest, open_url=open_url)
            self.assertTrue(dest.exists())


class TestTranscribeFile(unittest.TestCase):
    """모델은 안 부른다 — 없어도 도는 것만 본다."""

    def test_missing_dependency_is_explained(self):
        """의존성이 없을 때 메시지가 설치법을 알려주는가."""
        import builtins
        real = builtins.__import__

        def fake(name, *a, **kw):
            if name.startswith("faster_whisper"):
                raise ImportError("no module")
            return real(name, *a, **kw)

        builtins.__import__ = fake
        try:
            with self.assertRaises(RuntimeError) as cm:
                transcribe.transcribe_file("/tmp/none.mp4")
            self.assertIn("faster-whisper", str(cm.exception))
        finally:
            builtins.__import__ = real

    def test_import_does_not_require_whisper(self):
        """모듈 import 만으로는 무거운 의존성을 안 탄다 (의존성 0 규약)."""
        import importlib

        import ssu_agent.transcribe as t

        importlib.reload(t)     # 예외 없이 통과하면 된다


if __name__ == "__main__":
    unittest.main()
