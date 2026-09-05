# 요약 입구 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요약을 자동 파이프라인에 숨은 단계에서 사람이 현황을 보고 고르는 명령으로 옮긴다 — `refresh` 에서 요약을 빼고, `status` 로 현황을 보여주고, 무엇을 요약할지는 `sources` 하나로 정한다.

**Architecture:** 대상 선정(`_targets(sources=…)`)을 개념으로 끌어올려, 세는 쪽(`status.py`)과 돌리는 쪽(`summarize.run`)이 **같은 함수**를 보게 한다. 화면이 "2건"이라 하고 실행이 3건을 요약하는 어긋남을 구조로 막는다. `refresh` 는 LLM 을 부르지 않는 동기 입구로 되돌리고, cron 과 수동 실행의 충돌은 `flock` 으로 닫는다.

**Tech Stack:** Python 3 표준 라이브러리만 (`unittest`, `fcntl`, `contextlib`) · Next.js 14 / CommonJS 테스트(`node test/*.test.cjs`, `assert`) · 의존성 추가 **0**

**Spec:** `docs/superpowers/specs/2026-09-06-summarize-entry-design.md`
(선행: `~/eunzi-os/05_AI/작업중/2026-09-05_손업로드요약_설계.md` — §5 `_Args` 는 (b) 로 확정)

## Global Constraints

1. 🔴 **자동 경로는 손 업로드를 영원히 안 본다.** `sources` 기본값은 어디서나 `("ledger",)`. 03:00 cron 은 플래그를 안 켠다 → 자동 과금 0
2. 🔴 **세는 근거와 돌리는 근거는 같은 함수다.** `status` 는 `materials/` 를 자기 방식으로 훑지 않는다. 반드시 `_targets(sources=…)` 를 센다
3. 🔴 **`refresh` 는 LLM 을 부르지 않는다.** 이 계획이 끝나면 `refresh` 경로 어디에도 `summarize.run` 호출이 없다
4. 🔴 **대시보드는 LLM 을 안 부르고 vault 를 안 쓴다.** 요약 버튼을 만들지 않는다. `lib/vault.js` 무접촉, `meta.json`·`.progress/` 에 쓰지 않는다
5. 🔴 **파일명 비교는 NFC 정규화 후** (Python `unicodedata.normalize("NFC", …)`, JS `.normalize("NFC")`). 빠뜨리면 같은 PDF 를 두 번 요약한다
6. **dotfile 제외 · `.pdf` 만 대상** (`pymupdf4llm` 이 pptx·zip 을 못 읽는다)
7. 새 의존성 **0**. Python `python3 -m unittest discover -s tests -t .`, 대시보드 `npm test`
8. 대시보드 빌드·테스트는 nvm Node 20 PATH 필요: `source ~/.nvm/nvm.sh && nvm use 20`
9. `git add -A` 금지 — 만진 경로만 add. repo 3개(`~/ssu-agent`, `~/eunzi-os-dashboard`, `~/eunzi-os`)는 따로 커밋
10. **범위 밖(알고 남기는 것):** 같은 이름 재업로드 시 옛 `.progress` 로 건너뜀 · pptx 요약 · 대시보드 요약 버튼 · 출결·공지·퀴즈 현황판 · `status` 의 영상 칸(Phase 4)
11. `transcribe.py` **무접촉** — `summarize._targets` 를 직접 부르지 않고 `meta.items[*].file` 계약만 공유한다 (확인 완료)

---

### Task 1: `lock.py` — 겹쳐 돌면 뒤에 온 쪽이 빠진다

**Files:**
- Create: `src/ssu_agent/lock.py`
- Test: `tests/test_lock.py` (신규)

**Interfaces:**
- Consumes: 없음
- Produces: `lock.held(name, root=None)` — 컨텍스트 매니저. `with lock.held("summarize") as ok:` 에서 `ok` 는 `True`(잡음) / `False`(이미 누가 잡고 있음). 잠금 파일은 `state/{name}.lock`. Task 4 의 `cmd_summarize` 가 쓰고, 동영상 Phase 2 Task 4 가 `transcribe` 로 재사용한다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_lock.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_lock -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ssu_agent.lock'`

- [ ] **Step 3: `src/ssu_agent/lock.py` 를 만든다**

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_lock -v && python3 -m unittest discover -s tests -t .
```

Expected: `test_lock` 5개 PASS · 전체 스위트 OK

- [ ] **Step 5: 커밋**

```bash
cd ~/ssu-agent && git add src/ssu_agent/lock.py tests/test_lock.py \
  && git commit -m "feat(lock): state/{name}.lock — 크론과 수동 실행이 겹쳐도 두 번 요약하지 않는다"
```

---

### Task 2: `_targets(sources)` + `run`/`estimate` 로 전달

**Files:**
- Modify: `src/ssu_agent/summarize.py` — `_targets`(`:261-277`), `run`(`:319-320`), `estimate`(`:409-411`)
- Test: `tests/test_summarize.py` (파일 끝에 클래스 2개 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `_targets(semester, root=None, sources=("ledger",))` → `[(week_dir, content_id, filename, meta), …]`. 손 업로드 항목의 `content_id` 는 `"manual-" + filename`. `run(…, sources=("ledger",))` · `estimate(…, sources=("ledger",))`. Task 3 의 `status.collect` 와 Task 4 의 `cmd_summarize` 가 이 시그니처를 부른다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize.py` 맨 끝에 붙인다. 상단에 이미 `import json, pathlib, tempfile, unittest`, `from unittest import mock`, `from ssu_agent import summarize as sm`, 헬퍼 `wk(tmp, course, week)` 가 있다.

```python
class TargetsSources(unittest.TestCase):
    """🔴 대상 선정은 sources 하나로 정한다. 기본값은 장부만 —
    여기가 새면 03:00 cron 이 손 업로드를 LLM 에 보낸다(자동 과금 0 계약)."""

    def _mk(self, tmp):
        d = wk(tmp)                                  # 장부에 강의.pdf 하나
        m = d / "materials"
        (m / "손PDF.pdf").write_bytes(b"%PDF-1.7")
        (m / "교재.pptx").write_bytes(b"PK\x03\x04")
        (m / ".DS_Store").write_bytes(b"x")
        return d

    def _names(self, tmp, **kw):
        return sorted(f for _wd, _cid, f, _m
                      in sm._targets("2026-2", root=pathlib.Path(tmp), **kw))

    def test_default_is_ledger_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            self.assertEqual(self._names(tmp), ["강의.pdf"])

    def test_manual_only_skips_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            self.assertEqual(self._names(tmp, sources=("manual",)), ["손PDF.pdf"],
                             "'올린 것만' 을 표현할 수 있어야 상한 순서 문제가 사라진다")

    def test_both_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            self.assertEqual(self._names(tmp, sources=("ledger", "manual")),
                             ["강의.pdf", "손PDF.pdf"])

    def test_manual_content_id_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            got = sm._targets("2026-2", root=pathlib.Path(tmp), sources=("manual",))
            self.assertEqual(got[0][1], "manual-손PDF.pdf",
                             "LMS content_id 는 숫자라 이 접두사와 안 겹친다")

    def test_unknown_source_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            with self.assertRaises(ValueError):
                sm._targets("2026-2", root=pathlib.Path(tmp), sources=("전사본",))

    def test_pptx_and_dotfile_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            names = self._names(tmp, sources=("ledger", "manual"))
            self.assertNotIn("교재.pptx", names)
            self.assertNotIn(".DS_Store", names)

    def test_nfd_filename_does_not_leak_to_manual(self):
        """macOS 왕복이 NFD 를 만든다. 정규화를 빠뜨리면 장부 파일이 '장부 밖'으로
        갈려 같은 PDF 를 두 번 요약한다 — 돈이 두 배다."""
        import unicodedata
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            nfd = unicodedata.normalize("NFD", "강의.pdf")
            if nfd != "강의.pdf":
                (d / "materials" / "강의.pdf").rename(d / "materials" / nfd)
            got = sm._targets("2026-2", root=pathlib.Path(tmp),
                              sources=("ledger", "manual"))
            self.assertEqual([c for _w, c, _f, _m in got if c.startswith("manual-")],
                             [])

    def test_broken_meta_keeps_manual_and_protects_collected(self):
        """🔴 장부가 깨졌다고 '전부 장부 밖'으로 보면, 이미 요약된 수집본까지
        manual- 키로 다시 요약된다(돈 두 배). .progress 를 대체 장부로 쓴다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = self._mk(tmp)
            sm.save_progress(d, "cid1", {"file": "강의.pdf", "status": "done"})
            (d / "meta.json").write_text("{깨짐", encoding="utf-8")
            got = sm._targets("2026-2", root=pathlib.Path(tmp),
                              sources=("ledger", "manual"))
            names = sorted(f for _w, _c, f, _m in got)
            self.assertEqual(names, ["손PDF.pdf"], "수집본은 대체 장부가 지킨다")
            self.assertEqual(got[0][3], {}, "meta 는 빈 dict 로 넘어간다")

    def test_broken_meta_still_retries_failed_manual(self):
        """실패한 손 업로드는 키가 manual- 이라 대체 장부 규칙에 안 걸린다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = self._mk(tmp)
            sm.save_progress(d, "manual-손PDF.pdf",
                             {"file": "손PDF.pdf", "status": "failed"})
            (d / "meta.json").write_text("{깨짐", encoding="utf-8")
            names = self._names(tmp, sources=("manual",))
            self.assertIn("손PDF.pdf", names, "재시도가 막히면 안 된다")

    def test_broken_meta_with_ledger_only_skips_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._mk(tmp)
            (d / "meta.json").write_text("{깨짐", encoding="utf-8")
            self.assertEqual(self._names(tmp), [], "지금과 같은 동작")


class RunSources(unittest.TestCase):
    def _week(self, tmp):
        d = wk(tmp)
        (d / "materials" / "손PDF.pdf").write_bytes(b"%PDF-1.7")
        return d

    def _run(self, tmp, **kw):
        with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
            return sm.run("2026-2",
                          extract=lambda p: ("가" * 2000, {"pages": 13}),
                          llm=lambda prompt, **k: "요약본",
                          log=lambda *a: None, **kw)

    def test_default_does_not_touch_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._week(tmp)
            res = self._run(tmp)
            self.assertEqual(res["done"], 1)
            self.assertEqual(sm.load_progress(d, "manual-손PDF.pdf"), {},
                             "장부 기록조차 안 생긴다")

    def test_manual_only_leaves_ledger_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._week(tmp)
            res = self._run(tmp, sources=("manual",))
            self.assertEqual(res["done"], 1)
            self.assertEqual(res["skipped"], 0, "장부는 대상이 아니라 세지도 않는다")
            self.assertEqual(sm.load_progress(d, "cid1"), {})
            rec = sm.load_progress(d, "manual-손PDF.pdf")
            self.assertEqual(rec["status"], "done")
            self.assertEqual(rec["file"], "손PDF.pdf",
                             "대시보드가 rec.file 로 매칭한다 — 키 합성과 무관해야 한다")
            self.assertIn("## 손PDF.pdf",
                          (d / "summary.md").read_text(encoding="utf-8"))

    def test_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._week(tmp)
            self._run(tmp, sources=("manual",))
            res = self._run(tmp, sources=("manual",))
            self.assertEqual((res["done"], res["skipped"]), (0, 1))

    def test_estimate_respects_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._week(tmp)
            with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
                ex = lambda p: ("가" * 2000, {"pages": 13})
                self.assertEqual(sm.estimate("2026-2", extract=ex)["docs"], 1)
                self.assertEqual(
                    sm.estimate("2026-2", extract=ex, sources=("manual",))["docs"], 1)
                self.assertEqual(
                    sm.estimate("2026-2", extract=ex,
                                sources=("ledger", "manual"))["docs"], 2,
                    "어림과 실행이 갈리면 어림이 쓸모없다")
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_summarize.TargetsSources -v
```

Expected: FAIL — `TypeError: _targets() got an unexpected keyword argument 'sources'`

- [ ] **Step 3: `_targets` 를 바꾼다**

`src/ssu_agent/summarize.py:261` 의 `_targets` 를 통째로 교체하고, 파일 상단 import 블록(`import json / os / re / time`)에 `import unicodedata` 를 알파벳 순으로 끼워 넣는다.

```python
_SOURCES = ("ledger", "manual")


def _collected_files(wd):
    """`.progress` 를 대체 장부로 읽는다 — manual- 이 아닌 키의 레코드에 올라온
    파일명은 **수집본**이다. meta.json 이 깨졌을 때만 쓴다."""
    out = set()
    d = progress_dir(wd)
    try:
        names = sorted(p.name for p in d.iterdir())
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json") or n.startswith("manual-"):
            continue
        try:
            rec = json.loads((d / n).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f = rec.get("file") or ""
        if f:
            out.add(unicodedata.normalize("NFC", f))
    return out


def _targets(semester, root=None, sources=("ledger",)):
    """(주차디렉터리, content_id, 파일명, meta) — 요약 대상.

    sources:
      "ledger"  meta.json items 의 .pdf 이고 materials/{f} 가 있는 것
      "manual"  장부 밖 materials/*.pdf. content_id = "manual-{파일명}"

    🔴 기본값이 ("ledger",) 인 것이 자동 과금 0 계약이다 — 03:00 cron 과
    사람이 안 시킨 모든 경로는 손 업로드를 영원히 안 본다.
    Phase 4 가 "transcript" 를 여기 한 줄로 더한다.
    """
    bad = [s for s in sources if s not in _SOURCES]
    if bad:
        raise ValueError("모르는 source: " + ", ".join(bad))
    want = set(sources)
    base = (root or DATA_DIR) / semester
    out = []
    if not base.is_dir():
        return out
    for course in sorted(p for p in base.iterdir() if p.is_dir()):
        for wd in sorted(p for p in course.iterdir() if p.is_dir()):
            try:
                meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
                broken = False
            except (OSError, ValueError):
                if "manual" not in want:
                    continue          # 지금과 같은 동작 — 이 주차를 건너뛴다
                meta, broken = {}, True

            ledger = set()
            for cid, rec in sorted((meta.get("items") or {}).items()):
                f = rec.get("file") or ""
                if f:
                    ledger.add(unicodedata.normalize("NFC", f))
                if ("ledger" in want and f.lower().endswith(".pdf")
                        and (wd / "materials" / f).exists()):
                    out.append((wd, cid, f, meta))
            if broken:
                # 장부를 못 읽었다. 이미 요약 장부에 있는 파일은 수집본이다 —
                # 이걸 안 지키면 요약된 LMS PDF 가 manual- 키로 다시 요약된다.
                ledger |= _collected_files(wd)

            if "manual" not in want:
                continue
            md = wd / "materials"
            if not md.is_dir():
                continue
            for p in sorted(md.iterdir()):
                n = p.name
                if n.startswith(".") or not p.is_file():
                    continue
                if not n.lower().endswith(".pdf"):
                    continue                      # pptx·zip 은 범위 밖
                if unicodedata.normalize("NFC", n) in ledger:
                    continue
                out.append((wd, "manual-" + n, n, meta))
    return out
```

- [ ] **Step 4: `run` 과 `estimate` 에 전달한다**

본문 로직은 손대지 않고 시그니처와 `_targets` 호출만 바꾼다.

```python
def run(semester, extract=extract_pdf, llm=call_llm, chunk_size=CHUNK_CHARS,
        max_calls=MAX_CALLS, root=None, log=print, sources=("ledger",)):
    res = {"done": 0, "skipped": 0, "failed": 0, "unsupported": 0,
           "calls": 0, "budget_hit": False, "in_tokens": 0, "out_tokens": 0}
    for wd, cid, fname, meta in _targets(semester, root, sources):
```

```python
def estimate(semester, extract=extract_pdf, chunk_size=CHUNK_CHARS, root=None,
             sources=("ledger",)):
    """키 없이 도는 눈대중. 실제 청구서가 아니다."""
    e = {"docs": 0, "unsupported": 0, "skipped": 0, "chars": 0, "chunks": 0}
    for wd, cid, fname, _meta in _targets(semester, root, sources):
```

- [ ] **Step 5: 통과를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK — `TargetsSources` 10개 · `RunSources` 4개 포함, 기존 테스트 회귀 0

- [ ] **Step 6: 커밋**

```bash
cd ~/ssu-agent && git add src/ssu_agent/summarize.py tests/test_summarize.py \
  && git commit -m "feat(summarize): 대상 선정을 sources 로 — ledger/manual, 깨진 장부는 .progress 로 방어"
```

---

### Task 3: `status.py` — 장부만 세는 순수 뷰

**Files:**
- Create: `src/ssu_agent/status.py`
- Test: `tests/test_status.py` (신규)

**Interfaces:**
- Consumes: Task 2 의 `_targets(semester, root, sources)` · 기존 `summarize.load_progress`
- Produces: `status.collect(semester, root=None) -> dict` (아래 형태) · `status.render(st) -> str` · `status.pending_line(st) -> str`. Task 4 의 `cmd_status` 와 Task 5 의 `cmd_refresh` 가 쓴다

```python
{"semester": "2026-2",
 "ledger": {"done": 3, "pending": 5, "failed": 0, "unsupported": 2},
 "manual": {"done": 1, "pending": 2, "failed": 0, "unsupported": 0},
 "video": None,                      # Phase 4 가 채운다
 "last_progress_at": "2026-09-05 03:14"}   # 없으면 None
```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_status.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_status -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ssu_agent.status'`

- [ ] **Step 3: `src/ssu_agent/status.py` 를 만든다**

```python
# -*- coding: utf-8 -*-
"""요약 현황 — 장부를 세기만 한다. **네트워크도 LLM 도 안 탄다.**

2026-09-02 에 429 로 5건이 실패했을 때 `state/cron.log` 를 직접 뒤져야 했다.
사람이 "무엇이 얼마나 남았는지" 보고 고를 수 있어야 요약이 예측 가능해진다.

🔴 **세는 근거는 `summarize._targets` 하나다.** 여기서 materials/ 를 따로 훑으면
화면이 "2건"이라 하고 실행이 3건을 요약하는 어긋남이 난다 — 대시보드에서 장부
계약이 세 군데로 흩어져 NFC 를 한쪽만 빠뜨렸을 때 났던 것과 같은 병이다.

`미요약` 은 **받아둔 것 중 안 된 것**이다. 아직 안 받은 자료는 materials/ 에
없어서 _targets 가 모른다 — 미공개 자료 수는 `materials --dry-run` 의 소관이다.
"""

from . import summarize

_EMPTY = {"done": 0, "pending": 0, "failed": 0, "unsupported": 0}


def _count(semester, root, sources):
    c = dict(_EMPTY)
    last = None
    for wd, cid, _fname, _meta in summarize._targets(semester, root, sources):
        rec = summarize.load_progress(wd, cid)
        s = rec.get("status")
        if s == "done":
            c["done"] += 1
        elif s == "unsupported_scanned":
            c["unsupported"] += 1
        elif s == "failed":
            c["failed"] += 1
        else:
            # 없음 · in_progress — 중단된 것은 다음 실행이 이어받으므로 '남은 것'이다
            c["pending"] += 1
        at = rec.get("updated_at")
        if at and (last is None or at > last):
            last = at
    return c, last


def collect(semester, root=None):
    """순수 함수. 장부만 읽어 dict 를 낸다."""
    ledger, l1 = _count(semester, root, ("ledger",))
    manual, l2 = _count(semester, root, ("manual",))
    last = max([x for x in (l1, l2) if x], default=None)
    return {"semester": semester, "ledger": ledger, "manual": manual,
            "video": None,                 # Phase 4 (스펙 §8.2)
            "last_progress_at": last}


def _line(label, c):
    s = "  {}   완료 {} · 미요약 {} · 실패 {}".format(
        label, c["done"], c["pending"], c["failed"])
    if c["unsupported"]:
        s += " · 스캔불가 {}".format(c["unsupported"])
    return s


def render(st):
    """코코봇이 그대로 보내는 텍스트. 마크업은 안 붙인다 (brief 규약)."""
    lines = ["📊 요약 현황 ({})".format(st["semester"]),
             _line("자동 수집", st["ledger"]),
             _line("직접 올림", st["manual"])]
    if st.get("video"):
        lines.append(_line("동영상   ", st["video"]))
    if st.get("last_progress_at"):
        lines.append("  마지막 장부 갱신 {}".format(st["last_progress_at"]))
    return "\n".join(lines) + "\n"


def pending_line(st):
    """refresh 보고 끝에 붙는 한 줄. 장부만 읽으므로 공짜다."""
    a, b = st["ledger"]["pending"], st["manual"]["pending"]
    if not a and not b:
        return "📊 미요약 없음"
    return ("📊 미요약 — 자동 수집 {}건 · 직접 올림 {}건   "
            "「요약해줘」 라고 하면 골라서 돌린다".format(a, b))
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK — `Collect` 6개 · `Render` 4개 포함

- [ ] **Step 5: 커밋**

```bash
cd ~/ssu-agent && git add src/ssu_agent/status.py tests/test_status.py \
  && git commit -m "feat(status): 요약 현황 — _targets 를 세기만 하는 순수 뷰"
```

---

### Task 4: CLI — `status` 명령 · `--manual-only` · 잠금

**Files:**
- Modify: `src/ssu_agent/cli.py` — 모듈 docstring, `cmd_summarize`(`:219-251`), `class _Args`(`:269-271`), `summarize` 서브파서(`:377-384`), `build_parser` 에 `status` 추가
- Test: `tests/test_cli_summarize.py` (신규)

**Interfaces:**
- Consumes: Task 1 `lock.held` · Task 2 `run/estimate(sources=…)` · Task 3 `status.collect/render`
- Produces: `ssu-agent status [--json]` · `ssu-agent summarize [--include-manual | --manual-only]` · `cli.REFRESH_STEP_FIELDS` 와 필드 누락에서 터지는 `cli._Args`. Task 5 의 `cmd_refresh` 가 `_Args` 를 쓴다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli_summarize.py`:

```python
# -*- coding: utf-8 -*-
"""CLI — sources 플래그 · status 명령 · 잠금 · _Args 필드 누락 방어.

🔴 `_Args` 자리에서 두 번 밟았다. 2026-09-05 `--limit=None` 사고가 이 껍데기의
조용한 통과에서 났다. 필드를 빠뜨리면 **생성 시점에** 터지게 한다.
"""
import unittest

from ssu_agent import cli


class ArgsShell(unittest.TestCase):
    def test_missing_field_raises(self):
        with self.assertRaises(TypeError):
            cli._Args(cli.REFRESH_STEP_FIELDS, refresh=False, dry_run=True)

    def test_complete_set_ok(self):
        a = cli._Args(cli.REFRESH_STEP_FIELDS,
                      refresh=False, dry_run=True, verbose=False)
        self.assertTrue(a.dry_run)

    def test_without_fields_stays_permissive(self):
        self.assertFalse(cli._Args(models=False).models)


class Parser(unittest.TestCase):
    def test_summarize_defaults_off(self):
        a = cli.build_parser().parse_args(["summarize"])
        self.assertFalse(a.include_manual)
        self.assertFalse(a.manual_only)

    def test_manual_only(self):
        a = cli.build_parser().parse_args(["summarize", "--manual-only"])
        self.assertTrue(a.manual_only)

    def test_flags_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(
                ["summarize", "--include-manual", "--manual-only"])

    def test_status_command_exists(self):
        a = cli.build_parser().parse_args(["status"])
        self.assertFalse(a.json)


class Sources(unittest.TestCase):
    def test_translation(self):
        f = cli._sources
        self.assertEqual(f(cli._Args(include_manual=False, manual_only=False)),
                         ("ledger",))
        self.assertEqual(f(cli._Args(include_manual=True, manual_only=False)),
                         ("ledger", "manual"))
        self.assertEqual(f(cli._Args(include_manual=False, manual_only=True)),
                         ("manual",))


class Locking(unittest.TestCase):
    def test_busy_lock_exits_zero_without_running(self):
        """잠금 실패는 에러가 아니다 — 크론 로그가 매일 빨개지면 안 된다."""
        import contextlib
        import io

        called = []
        orig_run, orig_held = cli.summarize.run, cli.lock.held
        cli.summarize.run = lambda *a, **k: called.append(1)

        @contextlib.contextmanager
        def busy(name, root=None):
            yield False

        cli.lock.held = busy
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_summarize(cli._Args(
                    models=False, estimate=False, limit=5,
                    include_manual=False, manual_only=False))
        finally:
            cli.summarize.run, cli.lock.held = orig_run, orig_held
        self.assertEqual(rc, 0)
        self.assertEqual(called, [], "이미 돌고 있으면 실행하지 않는다")
        self.assertIn("이미", buf.getvalue())

    def test_estimate_is_not_locked(self):
        """읽기만 하는 명령을 잠그면 '현황 좀 보려다 막히는' 일이 생긴다."""
        import contextlib
        import io

        seen = []
        orig_est, orig_held = cli.summarize.estimate, cli.lock.held
        cli.summarize.estimate = lambda sem, **k: {
            "docs": 0, "chars": 0, "chunks": 0, "unsupported": 0, "skipped": 0,
            "est_input_tokens": 0, "est_output_tokens": 0, "est_usd": 0.0}

        @contextlib.contextmanager
        def spy(name, root=None):
            seen.append(name)
            yield True

        cli.lock.held = spy
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_summarize(cli._Args(
                    models=False, estimate=True, limit=5,
                    include_manual=False, manual_only=False))
        finally:
            cli.summarize.estimate, cli.lock.held = orig_est, orig_held
        self.assertEqual(seen, [], "estimate 는 잠그지 않는다")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_cli_summarize -v
```

Expected: FAIL — `AttributeError: module 'ssu_agent.cli' has no attribute 'REFRESH_STEP_FIELDS'`

- [ ] **Step 3: `cli.py` 를 고친다 (6곳)**

**(1)** 상단 import 에 `lock`, `status` 를 기존 import 줄들과 같은 방식으로 추가한다 (`from . import ... summarize ...` 형태를 따른다).

**(2)** 모듈 docstring 의 `summarize` 줄 아래에 두 줄을 넣는다:

```
    ssu-agent summarize           자료 → 마크다운 → LLM 요약 (--estimate 로 비용 먼저)
                                  --include-manual 로 손 업로드까지, --manual-only 로 그것만
    ssu-agent status              요약 현황. 네트워크도 LLM 도 안 탄다 (--json)
```

**(3)** `class _Args` 를 바꾸고 위에 필드 선언을 둔다:

```python
# _Args 로 껍데기를 만들 때 이 목록을 넘기면 🔴 필드를 빠뜨린 자리에서
# **즉시** TypeError 로 터진다. 2026-09-05 `--limit=None` 사고가 이 껍데기의
# 조용한 통과에서 났다 — 요약 단계가 통째로 죽었고 원인을 찾는 데 시간이 걸렸다.
REFRESH_STEP_FIELDS = ("refresh", "dry_run", "verbose")


class _Args(object):
    def __init__(self, _fields=None, **kw):
        if _fields:
            missing = [f for f in _fields if f not in kw]
            if missing:
                raise TypeError("_Args 필드 누락: %s" % ", ".join(missing))
        self.__dict__.update(kw)
```

**(4)** `cmd_summarize` 위에 `_sources` 를 두고, `cmd_summarize` 의 estimate/run 부분을 바꾼다:

```python
def _sources(a):
    """플래그 → sources. 기본은 장부만 — 자동 과금 0 계약이다."""
    if getattr(a, "manual_only", False):
        return ("manual",)
    if getattr(a, "include_manual", False):
        return ("ledger", "manual")
    return ("ledger",)
```

```python
    if a.estimate:
        # 🔴 잠그지 않는다 — estimate 는 장부에도 markdown/ 에도 쓰지 않는다
        e = summarize.estimate(sem, sources=_sources(a))
```

```python
    with lock.held("summarize") as ok:
        if not ok:
            _sum("이미 요약이 돌고 있어. 「현황 봐줘」 로 확인해")
            return 0                      # 잠금 실패는 에러가 아니다
        try:
            res = summarize.run(sem, max_calls=a.limit, sources=_sources(a))
        except Exception as e:
            raise SystemExit("요약 실패 — %s: %s"
                             % (type(e).__name__, str(e).split("\n")[0][:160]))
```

(그 아래 `_sum("요약 {done} …")` 부터 `return 0` 까지는 **들여쓰기만** `with` 안으로 옮긴다. 문구는 그대로.)

**(5)** `cmd_status` 를 추가한다 (`cmd_summarize` 아래):

```python
def cmd_status(a):
    """요약 현황. 네트워크도 LLM 도 안 탄다 — 돈이 안 든다."""
    st = status.collect(get().semester)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    sys.stdout.write(status.render(st))
    return 0
```

**(6)** 파서 두 곳:

```python
    su = sub.add_parser("summarize")
    ...
    g = su.add_mutually_exclusive_group()
    g.add_argument("--include-manual", action="store_true",
                   help="손으로 올린 PDF 까지 함께 (기본 꺼짐)")
    g.add_argument("--manual-only", action="store_true",
                   help="손으로 올린 PDF 만")

    stp = sub.add_parser("status", help="요약 현황 (돈 안 씀)")
    stp.add_argument("--json", action="store_true", help="코코봇이 받아갈 구조")
    stp.set_defaults(func=cmd_status)
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK — 전부 통과

- [ ] **Step 5: 실제로 돌려본다 (돈 안 씀)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent status && ./bin/ssu-agent summarize --help
```

Expected: 현황 3~4줄이 뜨고, `--include-manual`/`--manual-only` 가 도움말에 보인다

- [ ] **Step 6: 커밋**

```bash
cd ~/ssu-agent && git add src/ssu_agent/cli.py tests/test_cli_summarize.py \
  && git commit -m "feat(cli): status 명령 · --manual-only/--include-manual · 요약 잠금"
```

---

### Task 5: `refresh` 에서 요약을 뺀다

**Files:**
- Modify: `src/ssu_agent/refresh.py` — docstring, `STEPS`, `LABEL`
- Modify: `src/ssu_agent/cli.py` — `cmd_refresh`(`:293-330`), `refresh` 서브파서(`:386-397`)
- Test: `tests/test_refresh.py` (4단계 기대 수정) · `tests/test_cli_summarize.py` (현황 줄 추가)

**Interfaces:**
- Consumes: Task 3 `status.collect/pending_line` · Task 4 `_Args`/`REFRESH_STEP_FIELDS`
- Produces: `refresh.STEPS == ("sync", "vault", "materials")`. 이 계획이 끝나면 refresh 경로 어디에도 `summarize.run` 호출이 없다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_refresh.py` 의 `test_runs_all_four_in_order` 를 고치고 클래스 하나를 더한다:

```python
class Order(unittest.TestCase):
    def test_runs_all_three_in_order(self):
        seen = []

        def rec(n, line):
            def f():
                seen.append(n)
                return {"ok": True, "line": line}
            return f

        res = rf.run({k: rec(k, k + " 됨") for k in rf.STEPS})
        self.assertEqual(seen, list(rf.STEPS))
        self.assertFalse(res["aborted"])
        self.assertEqual(len(res["steps"]), 3)


class NoSummaryStep(unittest.TestCase):
    """🔴 refresh 는 LLM 을 부르지 않는다. '업데이트해줘' 한 마디에 돈이
    딸려 나가면 안 된다 — 스킬에 경고를 달아야 했던 것이 그 증거다."""

    def test_steps_have_no_summary(self):
        self.assertEqual(rf.STEPS, ("sync", "vault", "materials"))
        self.assertNotIn("summary", rf.LABEL)

    def test_unknown_step_still_rejected(self):
        with self.assertRaises(ValueError):
            rf.run({}, want=("summary",))
```

`tests/test_cli_summarize.py` 에 추가:

```python
class RefreshHasNoSummary(unittest.TestCase):
    def test_parser_dropped_summary_flags(self):
        p = cli.build_parser()
        for bad in (["refresh", "--no-summary"], ["refresh", "--limit", "5"]):
            with self.assertRaises(SystemExit):
                p.parse_args(bad)

    def test_reports_pending_line(self):
        """🔴 fns 를 부르지 않는다 — 부르면 진짜 sync 가 돌아 네트워크를 탄다."""
        import contextlib
        import io

        orig_run, orig_collect, orig_get = (
            cli.refresh.run, cli.status.collect, cli.get)
        cli.refresh.run = lambda fns, want=(): {"steps": [], "aborted": False}
        cli.get = lambda: cli._Args(semester="2026-2")
        cli.status.collect = lambda sem, root=None: {
            "semester": sem,
            "ledger": {"done": 0, "pending": 5, "failed": 0, "unsupported": 0},
            "manual": {"done": 0, "pending": 2, "failed": 0, "unsupported": 0},
            "video": None, "last_progress_at": None}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli.cmd_refresh(cli._Args(no_materials=True, dry_run=True,
                                          verbose=False))
        finally:
            cli.refresh.run, cli.status.collect, cli.get = (
                orig_run, orig_collect, orig_get)
        out = buf.getvalue()
        self.assertIn("미요약", out)
        self.assertIn("5", out)
        self.assertIn("2", out)
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_refresh tests.test_cli_summarize -v
```

Expected: FAIL — `AssertionError: ('sync','vault','materials','summary') != ('sync','vault','materials')`

- [ ] **Step 3: `refresh.py` 를 고친다**

```python
STEPS = ("sync", "vault", "materials")

LABEL = {"sync": "① 수집   ", "vault": "② vault  ", "materials": "③ 자료   "}
```

docstring 첫 줄과 마지막 문단을 고친다 — *"수집·반영·자료를 한 번에"*, 그리고
*"자료 하나가 실패해도 요약은 돈다"* → *"자료 하나가 실패해도 나머지는 돈다"*.
docstring 어딘가에 **왜 요약이 빠졌는지** 한 줄을 남긴다:

```
🔴 **요약은 여기 없다.** 느리고·돈 쓰고·예산을 지는 단계를 동기 대화형 입구에
넣지 않는다(동영상 전사를 안 붙이는 §8.5 와 같은 논리). 현황은 끝에 한 줄로
보여주고, 요약은 사람이 `summarize` 로 시킨다.
```

- [ ] **Step 4: `cmd_refresh` 와 파서를 고친다**

```python
def cmd_refresh(a):
    """수집 → vault → 자료. 코코봇이 부르는 입구다 (refresh.py 참고).

    🔴 LLM 을 부르지 않는다. 요약은 `summarize` 가 따로 진다.
    """
    want = list(refresh.STEPS)
    if a.no_materials:
        want.remove("materials")

    fresh = _Args(REFRESH_STEP_FIELDS, refresh=False, dry_run=a.dry_run,
                  verbose=False)
    ...
    fns = {
        "sync": _sync,
        "vault": lambda: _quiet(cmd_vault_sync, fresh),
        "materials": lambda: _quiet(cmd_materials, fresh),
    }
    res = refresh.run(fns, want=tuple(want))
    if a.verbose:
        for s in res["steps"]:
            print("--- {} ---".format(s["name"]))
            print(s.get("detail") or s["line"])
    sys.stdout.write(refresh.render(res))
    # 현황 한 줄 — 장부만 읽으므로 공짜다. 중단됐으면 내지 않는다:
    # 낡은 장부로 센 숫자를 보여주지 않는다.
    if not res["aborted"]:
        print(status.pending_line(status.collect(get().semester)))
    return 1 if res["aborted"] else 0
```

파서에서 `--no-summary` 와 `--limit` 를 **삭제**한다. `--no-materials`·`--dry-run`·`--verbose` 는 그대로 두고, `--no-materials` 의 help 를 `"자료 다운로드 건너뛰기"` 로 고친다(요약 언급 제거).

- [ ] **Step 5: 통과를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t . \
  && grep -rn "summarize" src/ssu_agent/refresh.py \
  && echo "🔴 refresh.py 에 summarize 가 남아있다" || echo "✅ refresh 경로에 요약 없음"
```

Expected: 전체 OK · 마지막 줄 `✅ refresh 경로에 요약 없음`

- [ ] **Step 6: 커밋**

```bash
cd ~/ssu-agent && git add src/ssu_agent/refresh.py src/ssu_agent/cli.py \
  tests/test_refresh.py tests/test_cli_summarize.py \
  && git commit -m "refactor(refresh): 요약 단계 제거 — 입구는 돈을 안 쓰고 현황만 알린다"
```

---

### Task 6: cron 래퍼 — 요약 뒤에 현황 한 줄

**Files:**
- Modify: `bin/ssu-agent-materials-cron`

**Interfaces:**
- Consumes: Task 4 의 `ssu-agent status`
- Produces: `state/cron.log` 에 매일 현황 한 줄

- [ ] **Step 1: 래퍼를 고친다**

`./bin/ssu-agent summarize` 블록 **뒤**에, 성공/실패와 무관하게 현황을 찍는다. 주석으로 왜인지 남긴다:

```bash
# 손 업로드는 이 잡이 영원히 안 본다(자동 과금 0). 대신 **몇 건 남았는지는**
# 남긴다 — 은지가 올려놓고 잊으면 아무도 안 알려주기 때문이다. 돈 0·네트워크 0.
./bin/ssu-agent status >> "$LOG" 2>&1 || true
```

🔴 `summarize` 의 성공 분기 안이 아니라 **바깥**에 둔다. 요약이 실패한 날일수록 현황이 필요하다.

- [ ] **Step 2: 실제로 돌려본다**

```bash
cd ~/ssu-agent && ./bin/ssu-agent status >> state/cron.log && tail -5 state/cron.log
```

Expected: 현황이 로그에 남는다 (`state/` 는 gitignore 라 워킹트리가 안 더러워진다)

- [ ] **Step 3: cron 스냅샷을 갱신한다**

```bash
python3 ~/eunzi-tools/bin/cron_snapshot.py --write
```

- [ ] **Step 4: 커밋**

```bash
cd ~/ssu-agent && git add bin/ssu-agent-materials-cron \
  && git commit -m "cron: 요약 뒤에 현황 한 줄 — 손 업로드가 조용히 쌓이지 않게"
```

---

### Task 7: 대시보드 — 손 업로드도 `.progress` 를 먼저 본다

**Files:**
- Modify: `~/eunzi-os-dashboard/lib/study.js` (`listMaterials` 의 manual 분기)
- Modify: `~/eunzi-os-dashboard/app/components/MaterialTabs.tsx` (`SUM_LABEL`, `why()`)
- Test: `~/eunzi-os-dashboard/test/study.test.cjs`

**Interfaces:**
- Consumes: Task 2 가 쓰는 `.progress/manual-{파일명}.json` (레코드의 `file` 이 실제 파일명 — `summaryStatuses()` 가 그걸로 매칭하므로 키 합성과 무관하다)
- Produces: `materials[]` 의 `summary` 가 `manual` · `unsupported_manual` · 실제 상태값

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test/study.test.cjs` 의 "손으로 넣은 파일" 블록 아래에 붙인다. 상단 `sandbox()`(장부에 `강의노트.pdf` 하나)를 그대로 쓴다.

```js
// ── 손 업로드도 요약될 수 있다 (summarize --manual-only).
//    .progress 를 먼저 보고, 기록이 없을 때만 "미요약"이다.
{
  const { root, w } = sandbox();
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  fs.mkdirSync(path.join(w, ".progress"), { recursive: true });
  fs.writeFileSync(path.join(w, ".progress", "manual-손PDF.pdf.json"), JSON.stringify({
    file: "손PDF.pdf", status: "done",
  }), "utf8");
  const m = Object.fromEntries(
    study.readWeek("2026-2", "선형대수", 3, root).materials.map((x) => [x.name, x]));
  assert.strictEqual(m["손PDF.pdf"].source, "manual", "장부 밖인 건 그대로다");
  assert.strictEqual(m["손PDF.pdf"].summary, "done",
    "요약됐으면 요약됐다고 말해야 한다 — manual 로 덮으면 화면이 거짓말한다");
}
{
  const { root, w } = sandbox();
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  fs.mkdirSync(path.join(w, ".progress"), { recursive: true });
  fs.writeFileSync(path.join(w, ".progress", "manual-손PDF.pdf.json"), JSON.stringify({
    file: "손PDF.pdf", status: "failed", last_error: "429 rate limit",
  }), "utf8");
  const m = Object.fromEntries(
    study.readWeek("2026-2", "선형대수", 3, root).materials.map((x) => [x.name, x]));
  assert.strictEqual(m["손PDF.pdf"].summary, "failed");
  assert.strictEqual(m["손PDF.pdf"].summaryNote, "429 rate limit",
    "손 업로드도 실패 사유를 보여준다");
}
{
  const { root, w } = sandbox();
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  const m = Object.fromEntries(
    study.readWeek("2026-2", "선형대수", 3, root).materials.map((x) => [x.name, x]));
  assert.strictEqual(m["손PDF.pdf"].summary, "manual", "아직 안 시킨 것");
  assert.strictEqual(m["손PDF.pdf"].summaryNote, null);
}
{
  const { root, w } = sandbox();
  fs.writeFileSync(path.join(w, "materials", "교재.pptx"), "PK\x03\x04");
  fs.writeFileSync(path.join(w, "materials", "묶음.zip"), "PK\x03\x04");
  const m = Object.fromEntries(
    study.readWeek("2026-2", "선형대수", 3, root).materials.map((x) => [x.name, x]));
  assert.strictEqual(m["교재.pptx"].summary, "unsupported_manual",
    "시켜도 안 되는 걸 '미요약'이라 하면 안 된다");
  assert.strictEqual(m["묶음.zip"].summary, "unsupported_manual");
}
{
  const { root, w } = sandbox();   // 장부 파일은 아무것도 안 바뀐다
  fs.mkdirSync(path.join(w, ".progress"), { recursive: true });
  fs.writeFileSync(path.join(w, ".progress", "abc.json"), JSON.stringify({
    file: "강의노트.pdf", status: "done",
  }), "utf8");
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  assert.strictEqual(got.materials[0].source, "lms");
  assert.strictEqual(got.materials[0].summary, "done");
}
{
  const { root, w } = sandbox();   // 🔴 meta.json 을 쓰지 않는다
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  const before = fs.readFileSync(path.join(w, "meta.json"), "utf8");
  study.readWeek("2026-2", "선형대수", 3, root);
  assert.strictEqual(fs.readFileSync(path.join(w, "meta.json"), "utf8"), before);
}
```

- [ ] **Step 2: 실패를 확인한다**

```bash
source ~/.nvm/nvm.sh && nvm use 20 && cd ~/eunzi-os-dashboard && node test/study.test.cjs
```

Expected: FAIL — `'manual' !== 'done'`

- [ ] **Step 3: `listMaterials` 의 manual 분기를 고친다**

`lib/study.js` 의 `if (!ledger[name.normalize("NFC")]) { … }` 블록을 바꾼다. `summaryStatuses` 와 `ledgerFiles` 는 **손대지 않는다**.

```js
    // 장부(meta.json items)에 없으면 손으로 넣은 파일이다. 03:00 cron 은 이
    // 파일을 영원히 안 본다 — 하지만 사람이 `summarize --manual-only` 로
    // 시킬 수 있으므로, .progress 에 기록이 있으면 그게 진실이다.
    const isPdf = name.toLowerCase().endsWith(".pdf");
    if (!ledger[name.normalize("NFC")]) {
      const ms = st[name];
      if (ms) {
        return { name, size, viewable: isPdf, source: "manual",
                 summary: ms.status || "pending", summaryNote: ms.note || null };
      }
      // 기록이 없다 — PDF 는 "아직 안 시킨 것", 그 외는 시켜도 안 되는 것
      // (pymupdf4llm 이 pptx·zip 을 못 읽는다).
      return { name, size, viewable: isPdf, source: "manual",
               summary: isPdf ? "manual" : "unsupported_manual", summaryNote: null };
    }
    const s = st[name] || {};
    return { name, size, viewable: isPdf,
             source: "lms", summary: s.status || "pending", summaryNote: s.note || null };
```

- [ ] **Step 4: `MaterialTabs.tsx` 를 고친다**

🔴 `SUM_LABEL[m.summary] || SUM_LABEL.pending` 이 폴백이라, `unsupported_manual` 을 안 넣으면 pptx 가 **「요약 대기」로 거짓말한다.**

```tsx
const SUM_LABEL: Record<string, [string, string]> = {
  done: ["요약됨", "ok"],
  unsupported_scanned: ["스캔 PDF", "warn"],
  failed: ["요약 실패", "bad"],
  in_progress: ["요약 중단", "warn"],
  pending: ["요약 대기", "muted"],
  // 손으로 올린 PDF — 03:00 cron 은 안 보지만 사람이 시키면 요약된다.
  // "직접 올림"은 출처만 말할 뿐 다음에 뭘 할 수 있는지 못 알려줬다.
  manual: ["미요약", "muted"],
  unsupported_manual: ["요약 미지원", "muted"],
};
```

`why()` 의 `candidates.length === 0` 분기 return 문만 바꾼다 (나머지 로직은 그대로 — 자동 경로는 여전히 손 업로드를 안 본다):

```tsx
    return "이 주차 자료는 전부 손으로 올린 파일입니다. LMS 장부 밖이라 자동 요약 대상이 아닙니다. "
      + "코코봇에 「요약해줘」 라고 하면 현황을 보여주고 골라서 돌립니다.";
```

- [ ] **Step 5: 통과를 확인한다**

```bash
source ~/.nvm/nvm.sh && nvm use 20 && cd ~/eunzi-os-dashboard \
  && npm test && npx tsc --noEmit && npm run build
```

Expected: 6개 스위트 통과 · tsc 에러 0 · 빌드 성공

- [ ] **Step 6: 커밋**

```bash
cd ~/eunzi-os-dashboard && git add lib/study.js test/study.test.cjs \
  app/components/MaterialTabs.tsx \
  && git commit -m "feat(univ): 손 업로드도 요약 상태를 보여준다 — 미요약/요약 미지원"
```

---

### Task 8: 코코봇 스킬 v1.2.0 — 🔴 `description` 이 핵심이다

**Files:**
- Modify: `~/.hermes/skills/eunzi/univ-save/SKILL.md`

**Interfaces:**
- Consumes: Task 4 의 `status`·`--manual-only`, Task 5 의 달라진 `refresh`
- Produces: 없음 (문서)

- [ ] **Step 1: 🔴 `description` 을 고친다 — 여기가 라우팅이다**

라우터는 **스킬 이름과 잘린 `description` 만** 본다 (`hermes-agent/agent/skill_utils.py:618`, 60자 초과면 `desc[:57] + "..."`). 본문 표는 스킬이 **선택된 뒤에야** 읽힌다. 지금 description 에 `요약` 이 없어서 **"요약해줘" 는 이 스킬에 도달하지 못한다.**

```yaml
description: '학교 수업·마감 기록 + LMS 동기화("업데이트해줘")·자료 요약("요약해줘")'
version: 1.2.0
```

- [ ] **Step 2: 길이를 기계로 확인한다**

```bash
cd ~/.hermes/hermes-agent && python3 -c "
import sys; sys.path.insert(0, '.')
from agent.skill_utils import extract_skill_description
d = '학교 수업·마감 기록 + LMS 동기화(\"업데이트해줘\")·자료 요약(\"요약해줘\")'
print(len(d), '자')
print(repr(extract_skill_description({'description': d})))
"
```

Expected: 60자 이하이고 반환값에 `...` 이 **없다**. 넘치면 줄여서 다시 잰다 — 🔴 **`요약해줘` 와 `업데이트해줘` 가 둘 다 살아남아야 한다.**

- [ ] **Step 3: "LMS 동기화" 절을 고친다**

`refresh` 가 이제 요약을 안 하므로 **경고를 지운다** — 해소한 것은 지워야 한다.

- *"한 번에 넷을 돈다 — ① 수집 → ② vault → ③ 자료 → ④ PDF 요약(LLM)"* → **셋** (`① 수집 → ② vault → ③ 자료`)
- *"④ 요약은 돈을 쓴다 … 은지가 '돈 쓰지 마'라고 하면 `--no-summary` 다"* 불릿 **삭제** (`--no-summary` 는 이제 없는 플래그다)
- 표의 `"돈 쓰지 말고" · "요약은 빼고" → refresh --no-summary` 행 **삭제**
- *"매일 03:00 에 launchd 가 이미 돌린다"* 는 그대로 둔다
- 한 줄 추가: **`refresh` 는 이제 LLM 을 안 쓴다 — 돈이 안 든다.** 끝에 미요약 현황이 한 줄 붙는다

- [ ] **Step 4: 요약 절을 새로 넣는다**

```markdown
### 자료 요약 (2026-09-06 신설)

**🔴 두 단계다. 현황을 먼저 보여주고, 은지가 고른 다음에 돌린다.**

| 은지가 하는 말 | 명령 |
|---|---|
| "요약해줘" · "요약 안 된 거 있어?" | ① `~/ssu-agent/bin/ssu-agent status` |
| → "내가 올린 것만" | ② `... summarize --manual-only` |
| → "전부" | ② `... summarize --include-manual` |
| "얼마 나올지부터 보자" | `... summarize --manual-only --estimate` |
| "현황 봐줘" · "진행상황" | `... status` |

`status` 는 **돈이 안 든다** — 장부만 읽는다. 출력을 그대로 보여주고
**뭘 돌릴지 물어라.** 네가 대신 고르지 마라.

- 🔴 **`summarize` 는 돈을 쓴다** (Claude API). 애매하면 `--estimate` 를 먼저
  돌려 예상 비용을 보여주고 물어라
- 🔴 **`refresh` 에는 요약이 없다.** "최신 LMS 업데이트해줘" 로는 요약이 안 된다 —
  예고 없이 돈을 쓰지 않으려고 **일부러** 뺐다
- **PDF 만 된다.** ppt/pptx/zip 은 「요약 미지원」이다. 안 되는 걸 되는 것처럼 말하지 마라
- **이미 돌고 있으면** `"이미 요약이 돌고 있어"` 가 나온다. 에러가 아니다 — 그대로 전해라
- 영상 전사는 **아직 안 된다**. 물어보면 "아직"이라고 답해라
- `timeout=600` 을 명시해라
```

- [ ] **Step 5: 🔴 라우팅을 실측한다 (은지가 직접)**

문자 수 계산만으로 끝내지 않는다 — 이 규칙 자체가 2026-09-02 에 실측으로 얻어진 것이다.

```bash
hermes gateway restart    # 스킬 인덱스를 다시 읽게 한다
```

텔레그램에서 코코봇에게 차례로 보낸다:

1. **"요약해줘"** → `status` 가 돌고 현황 + 선택지가 와야 한다
2. **"최신 LMS 업데이트해줘"** → `refresh` 가 돌아야 한다 (기존 트리거가 안 죽었는지)

둘 중 하나라도 봇이 되물으면 **description 이 잘렸거나 단서가 부족한 것이다** — Step 1 로 돌아가 문구를 고치고 다시 잰다.

- [ ] **Step 6: `doctor.sh` 로 대조한다**

```bash
bash ~/eunzi-tools/bin/doctor.sh; echo "exit=$?"
```

설계서 §4 표와 어긋나면 경고가 뜬다. Task 10 에서 문서를 갱신한 뒤 다시 돌린다.

---

### Task 9: 실측 — PDF 2건을 실제로 요약한다

**Files:** 없음 (실행·확인만)

**Interfaces:**
- Consumes: Task 1~8 전부
- Produces: `data/2026-2/{과목}/W01/summary.md` 의 손 업로드 섹션 · `.progress/manual-*.json` · Task 10 History 에 넣을 실측 숫자

**대상 (선행 설계 §4 실측):** 선형대수 W01 `Chapter 01. 선형대수학의 개요.pdf`(3.2MB) · 확장현실디자인 W01 `1.Oculus_Link_to_PC.pdf`(7.0MB). 같은 폴더의 `Chapter 00. 교재 소개.pptx` 는 대상이 아니어야 한다.

- [ ] **Step 1: 현황이 맞는지 본다 (돈 안 씀)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent status
```

Expected: `직접 올림   완료 0 · 미요약 2 · 실패 0` — pptx 는 안 세어진다

- [ ] **Step 2: 대상 목록을 눈으로 확인한다**

```bash
cd ~/ssu-agent && python3 -c "
from ssu_agent import summarize as sm
from ssu_agent.config import get
sem = get().semester
for _w, c, f, _m in sm._targets(sem, sources=('manual',)):
    print(' ', c, '·', f)
print('pptx 가 있으면 버그:',
      [f for _w,_c,f,_m in sm._targets(sem, sources=('manual',))
       if not f.lower().endswith('.pdf')])
"
```

Expected: 두 PDF 만. 마지막 줄은 `[]`

- [ ] **Step 3: 비용을 먼저 어림한다 (LLM 호출 없음)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent summarize --manual-only --estimate
```

Expected: 문서 2개와 예상 `$`. 🔴 **$1 을 넘으면 멈추고 은지에게 보고한다.**

- [ ] **Step 4: 실제로 돌린다 (🔴 여기서 돈이 나간다)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent summarize --manual-only --limit 8
```

Expected: `요약 2 · 건너뜀 0 · 스캔 0 · 실패 0` + 실제 토큰 줄.
🔴 `건너뜀` 이 0 이 아니면 장부가 대상에 섞인 것이다 — 멈추고 원인을 찾는다

- [ ] **Step 5: 잠금을 실측한다**

```bash
cd ~/ssu-agent && (./bin/ssu-agent summarize --manual-only & sleep 0.3; \
  ./bin/ssu-agent summarize --manual-only; wait)
```

Expected: 뒤에 온 쪽이 `이미 요약이 돌고 있어…` 로 빠지고 **exit 0**

- [ ] **Step 6: 결과가 남았는지 본다**

```bash
cd ~/ssu-agent
python3 -c "
import json, glob
for p in sorted(glob.glob('data/2026-2/*/W01/.progress/manual-*.json')):
    r = json.load(open(p)); print(r['status'], '·', r['file'])
"
grep -c "^## " "data/2026-2/선형대수/W01/summary.md"
./bin/ssu-agent status
```

Expected: `manual-*.json` 의 status 가 `done`, `file` 이 실제 파일명 · `status` 가 `직접 올림   완료 2 · 미요약 0`

- [ ] **Step 7: 🔴 자동 경로가 여전히 안 보는지 확인한다 (계약 검증)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent refresh --dry-run
```

Expected: 단계가 **셋**(수집·vault·자료)이고 요약 단계가 **없다**. 끝에 현황 한 줄이 붙는다

- [ ] **Step 8: 대시보드에서 눈으로 본다**

```bash
open "http://localhost:3001/univ/선형대수/1"
```

Expected: 손 업로드 PDF 배지가 **「요약됨」**, pptx 는 **「요약 미지원」**, 요약 탭에 새 섹션.
(3001 이 안 뜨면 `pm2 list` 확인 — 빌드·재시작은 nvm Node 20 PATH 필요)

- [ ] **Step 9: 숫자를 적어둔다**

Task 10 History 에 넣을 것 — 실제 입력·출력 토큰, 청구 예상액, 걸린 시간, `--estimate` 어림과의 차이.

---

### Task 10: 문서 갱신 (CLAUDE.md §0 — 이걸 해야 끝난 것이다)

**Files:**
- Modify: `~/ssu-agent/README.md` · `~/eunzi-os/05_AI/프로젝트.md` · `~/eunzi-os/05_AI/가이드.md` · `~/eunzi-os/05_AI/시스템/05_스킬 기능표.md` · `~/eunzi-os/05_AI/시스템/01_에르메스 설계.md` §4 · `~/eunzi-os/HISTORY.md` · `~/ssu-agent/docs/superpowers/specs/2026-09-05-video-transcription-design.md` · `~/ssu-agent/docs/superpowers/plans/2026-09-05-video-transcription-phase2.md` · `~/eunzi-os/05_AI/작업중/2026-09-05_손업로드요약_설계.md`
- Create: `~/eunzi-os/05_AI/시스템/History/2026-09-06_ssu-agent_요약입구.md`

**Interfaces:**
- Consumes: Task 9 의 실측 숫자
- Produces: 없음 (마지막)

- [ ] **Step 1: `ssu-agent/README.md`**

명령 목록에 `status` 와 `summarize --manual-only/--include-manual` 추가. 배치 표의 `com.eunzi.ssu-materials` 행에 *"손 업로드는 안 본다 · 끝에 현황을 남긴다"* 를 적는다. `refresh` 설명에서 요약을 뺀다.

- [ ] **Step 2: 동영상 스펙 2곳**

`specs/2026-09-05-video-transcription-design.md`:
- §3 함수 표에서 `status(semester)`·`pending(snap, semester)` 를 **`status.py` 소관**으로 옮긴다(왜 옮겼는지 한 줄: 자료 현황을 전사 모듈이 소유하면 계층이 뒤집힌다)
- §8.2 의 예시 화면에 「직접 올림」 칸을 넣고, `미요약` 은 **받아둔 것 중 안 된 것**이라 `대기 14(미공개)` 와 다르다는 걸 한 줄로 적는다
- §8.4 잠금 절에 *"`lock.py` 가 이미 있다 — `held("transcribe")` 를 쓴다"* 를 적는다

`plans/2026-09-05-video-transcription-phase2.md` Task 4(잠금)에 **"`ssu_agent.lock.held` 를 쓴다 (2026-09-06 신설). 새로 만들지 마라"** 한 줄.

- [ ] **Step 3: `05_AI/프로젝트.md`**

「대학 · v2 · ssu-agent」 `active` 절에 완료 한 줄. 🔴 후보 줄이 **대학 v2 절에** 있는지 확인한다 (2026-09-05 에 eunzi-os v3 절에 잘못 넣었다가 옮긴 적 있다).

- [ ] **Step 4: `05_AI/가이드.md`**

트리거 두 줄 — "요약해줘" → 현황 먼저 보여주고 고른다 · "최신 LMS 업데이트해줘" → **이제 돈 안 씀**.

- [ ] **Step 5: `05_스킬 기능표.md` · `01_에르메스 설계.md` §4**

`univ-save` v1.2.0, 트리거 추가. 🔴 **§2 대조표 숫자까지** 맞춘다.

- [ ] **Step 6: History + `HISTORY.md`**

`History/2026-09-06_ssu-agent_요약입구.md`, frontmatter 🔴 `component: hermes`. 본문에 남길 것: **왜 `refresh` 에서 요약을 뺐나**(돈이 숨어 있었다 · 경고를 달아야 했던 것이 증거) · 방화벽을 장부→`sources` 로 옮긴 구조 · 깨진 장부에서 `.progress` 를 대체 장부로 쓰는 이유(돈 두 배 방지) · `description` 라우팅 사고(본문 표는 선택된 뒤에야 읽힌다) · Task 9 실측 숫자.

```bash
cd ~/eunzi-os && python3 ~/eunzi-tools/bin/history_index.py --write
```

`HISTORY.md` 에 날짜별 한 줄.

- [ ] **Step 7: 작업중 노트를 닫는다**

`05_AI/작업중/2026-09-05_손업로드요약_설계.md` 의 `status` 를 완료로 바꾸고 이 스펙과 History 를 가리키게 한다. `tags` 에서 `todo` 제거. 🔴 **해소한 것은 지운다** — §5 의 "은지가 정하면 착수한다" 같은 미결 문구가 남으면 다음 세션이 또 멈춘다.

- [ ] **Step 8: 기계로 검증한다**

```bash
bash ~/eunzi-tools/bin/doctor.sh; echo "exit=$?"
```

Expected: `exit=0`. **설계서↔실제 대조가 여기서 잡힌다 — 손으로 대조하지 마라.**

- [ ] **Step 9: 커밋 (repo 2개, 따로)**

```bash
cd ~/ssu-agent && git add README.md docs/superpowers/specs docs/superpowers/plans \
  && git commit -m "docs: 요약 입구 재설계 — README·동영상 스펙 정합"
cd ~/eunzi-os && git add "05_AI/프로젝트.md" "05_AI/가이드.md" \
  "05_AI/시스템/05_스킬 기능표.md" "05_AI/시스템/01_에르메스 설계.md" \
  "05_AI/시스템/History/2026-09-06_ssu-agent_요약입구.md" \
  "05_AI/시스템/History/History.md" HISTORY.md \
  "05_AI/작업중/2026-09-05_손업로드요약_설계.md" \
  && git commit -m "요약 입구 재설계 — 문서 갱신"
```

🔴 vault 커밋은 **iMac 에서만**. `git add -A` 금지.
