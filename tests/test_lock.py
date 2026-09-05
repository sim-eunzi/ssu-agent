# -*- coding: utf-8 -*-
"""state/{name}.lock — 크론과 수동 실행이 겹쳐도 같은 문서를 두 번 요약하지 않는다.

잠금 실패는 **에러가 아니다.** 뒤에 온 쪽이 조용히 빠지고 다음 기회에 온다.
"""
import pathlib
import tempfile
import unittest

from ssu_agent import lock


class Held(unittest.TestCase):
    def test_acquires_when_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            with lock.held("summarize", root=pathlib.Path(tmp)) as ok:
                self.assertTrue(ok)

    def test_second_holder_gets_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with lock.held("summarize", root=root) as first:
                self.assertTrue(first)
                with lock.held("summarize", root=root) as second:
                    self.assertFalse(second, "겹치면 뒤에 온 쪽은 안 돈다")

    def test_released_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with lock.held("summarize", root=root):
                pass
            with lock.held("summarize", root=root) as ok:
                self.assertTrue(ok, "빠져나오면 풀린다")

    def test_released_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            try:
                with lock.held("summarize", root=root):
                    raise RuntimeError("터짐")
            except RuntimeError:
                pass
            with lock.held("summarize", root=root) as ok:
                self.assertTrue(ok, "예외로 빠져나와도 풀린다")

    def test_creates_state_dir(self):
        """수동 실행이 cron 래퍼보다 먼저일 수 있다 — state/ 가 없어도 돈다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "없는곳"
            with lock.held("summarize", root=root) as ok:
                self.assertTrue(ok)
            self.assertTrue((root / "state" / "summarize.lock").exists())


if __name__ == "__main__":
    unittest.main()
