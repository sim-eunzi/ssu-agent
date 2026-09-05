# -*- coding: utf-8 -*-
"""`state/{name}.lock` — 크론과 수동 실행의 충돌을 막는다.

🔴 **잠금 실패는 에러가 아니다.** 요약은 재개 장부로 돌아가므로, 뒤에 온 쪽은
조용히 빠지고 다음 기회에 이어받으면 된다. 예외를 던지면 크론 로그가 매일
빨개진다.

커널이 파일 디스크립터를 닫을 때 자동 해제하므로 **프로세스가 죽어도 잠금이
남지 않는다** (`study.py` 가 vault 에 쓰는 것과 같은 이유로 flock 을 쓴다).
"""

import fcntl
from contextlib import contextmanager

from .config import ROOT


def _lock_path(name, root=None):
    # config.ROOT 는 repo 루트다 (`Path(__file__).resolve().parents[2]`).
    # state/cron.log 와 같은 곳에 둔다 — state/* 는 이미 gitignore 다.
    base = root if root is not None else ROOT
    return base / "state" / ("%s.lock" % name)


@contextmanager
def held(name, root=None):
    """`with held("summarize") as ok:` — ok 가 False 면 남이 잡고 있다."""
    p = _lock_path(name, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(str(p), "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()
