# 손 업로드 자료 요약 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드로 손수 올린 PDF 를 `ssu-agent summarize --include-manual` 로 **사람이 시킬 때만** 요약하게 한다. 자동 경로(03:00 cron · `refresh`)는 손 업로드를 영원히 안 본다.

**Architecture:** 방화벽을 「장부(`meta.json.items`) 유무」에서 「`include_manual` 플래그 유무」로 옮긴다. `summarize._targets()` 가 플래그가 켜졌을 때만 `materials/` 를 직접 훑어 장부 밖 PDF 를 추가하고, `.progress` 키는 `manual-{파일명}` 으로 LMS `content_id`(숫자)와 절대 안 겹치게 둔다. 대시보드는 **읽기만** 한다 — 요약 버튼을 만들지 않으므로 대시보드는 계속 돈을 안 쓴다.

**Tech Stack:** Python 3 표준 라이브러리만 (`unittest`, 의존성 0 유지 — `pymupdf4llm`·LLM SDK 는 지금도 함수 안 import) · Next.js 14 / CommonJS 테스트(`node test/*.test.cjs`, `assert`)

**Spec:** `~/eunzi-os/05_AI/작업중/2026-09-05_손업로드요약_설계.md` (§5 는 2026-09-05 에 (b) 로 확정됨)

## Global Constraints

1. 🔴 **자동 경로는 손 업로드를 영원히 안 본다.** `include_manual` 기본값은 어디서나 `False`. 03:00 cron(`bin/ssu-agent-materials-cron`)과 `refresh` 는 플래그를 켜지 않는다 → 자동 과금 0 유지
2. 🔴 **`refresh.py` 무접촉.** 이 파일은 한 줄도 고치지 않는다
3. 🔴 **대시보드에 요약 버튼을 만들지 않는다.** 대시보드는 LLM 을 부르지 않고 vault 도 안 쓴다. 대시보드 변경은 `lib/study.js` 읽기 로직과 `MaterialTabs.tsx` 표시 문구뿐
4. 🔴 **`lib/vault.js` 무접촉.** vault 는 read-only
5. 🔴 **대시보드는 `meta.json` 과 `.progress/` 에 쓰지 않는다.** 읽기만 한다
6. **ppt/pptx 는 범위 밖** — `pymupdf4llm` 이 못 읽는다. 화면에는 `unsupported_manual`("요약 미지원")로 뜨고, `--include-manual` 은 `.pdf` 만 집는다
7. **파일명 비교는 NFC 정규화 후.** macOS 파일시스템·압축 왕복이 NFD 를 만든다 (Python `unicodedata.normalize("NFC", ...)`, JS `.normalize("NFC")`)
8. **dotfile 제외** — `.` 로 시작하는 이름은 대상이 아니다
9. 새 의존성 **0**. Python 은 `python3 -m unittest discover -s tests -t .`, 대시보드는 `npm test` 로 전부 돈다
10. `git add -A` 금지 — 만진 경로만 add. 두 repo(`~/ssu-agent`, `~/eunzi-os-dashboard`)는 **따로** 커밋한다
11. 대시보드 빌드·테스트는 nvm Node 20 PATH 가 필요하다: `source ~/.nvm/nvm.sh && nvm use 20`
12. **범위 밖(알고 남기는 것):** 파일명이 240바이트를 넘으면 `.progress/manual-{파일명}.json` 이 파일명 길이 상한에 걸린다. 실제 강의자료 이름은 그 근처도 안 가므로 이번에 방어하지 않는다

---

### Task 1: `_targets(include_manual)` — 장부 밖 PDF 를 걷는다

**Files:**
- Modify: `~/ssu-agent/src/ssu_agent/summarize.py:261-277` (`_targets`)
- Test: `~/ssu-agent/tests/test_summarize.py` (파일 끝에 새 클래스 추가)

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `_targets(semester, root=None, include_manual=False)` → `[(week_dir: pathlib.Path, content_id: str, filename: str, meta: dict), ...]`. 손 업로드 항목의 `content_id` 는 `"manual-" + filename`, `meta` 는 그 주차의 `meta.json` 내용(못 읽으면 `{}`). Task 2 의 `run()`/`estimate()` 가 이 시그니처를 그대로 부른다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`~/ssu-agent/tests/test_summarize.py` 파일 맨 끝에 붙인다. 파일 상단에는 이미 `import json, pathlib, tempfile, unittest`, `from ssu_agent import summarize as sm`, 그리고 헬퍼 `wk(tmp, course, week)` 가 있다 — 그대로 쓴다.

```python
class TargetsManual(unittest.TestCase):
    """🔴 손 업로드는 include_manual 이 켜졌을 때만 걷는다.
    기본값으로는 장부(meta.json items)의 PDF 만 — 자동 과금 0 계약이다."""

    def _mk(self, tmp):
        d = wk(tmp)                                  # 장부에 강의.pdf 하나
        m = d / "materials"
        (m / "손PDF.pdf").write_bytes(b"%PDF-1.7")
        (m / "교재.pptx").write_bytes(b"PK\x03\x04")
        (m / ".DS_Store").write_bytes(b"x")
        return d

    def test_default_keeps_ledger_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            got = sm._targets("2026-2", root=pathlib.Path(tmp))
            self.assertEqual([f for _wd, _cid, f, _m in got], ["강의.pdf"],
                             "기본값은 장부만 — 손 업로드가 새면 03:00 cron 이 돈을 쓴다")

    def test_include_manual_adds_manual_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            got = sm._targets("2026-2", root=pathlib.Path(tmp), include_manual=True)
            names = sorted(f for _wd, _cid, f, _m in got)
            self.assertEqual(names, ["강의.pdf", "손PDF.pdf"], "장부 파일은 그대로 남는다")
            cid = [c for _wd, c, f, _m in got if f == "손PDF.pdf"][0]
            self.assertEqual(cid, "manual-손PDF.pdf",
                             "LMS content_id 는 숫자라 manual- 접두사와 안 겹친다")

    def test_pptx_and_dotfile_are_not_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._mk(tmp)
            got = sm._targets("2026-2", root=pathlib.Path(tmp), include_manual=True)
            names = [f for _wd, _cid, f, _m in got]
            self.assertNotIn("교재.pptx", names, "pymupdf4llm 이 못 읽는다 — 범위 밖")
            self.assertNotIn(".DS_Store", names)

    def test_nfd_filename_is_not_double_counted(self):
        """macOS 왕복이 NFD 를 만든다. 정규화 없이 비교하면 장부에 있는 파일이
        '장부 밖' 으로 잘못 갈려 같은 PDF 를 두 번 요약한다 (돈이 두 배)."""
        import unicodedata
        with tempfile.TemporaryDirectory() as tmp:
            d = wk(tmp)
            nfd = unicodedata.normalize("NFD", "강의.pdf")
            if nfd != "강의.pdf":
                (d / "materials" / "강의.pdf").rename(d / "materials" / nfd)
            got = sm._targets("2026-2", root=pathlib.Path(tmp), include_manual=True)
            manual = [c for _wd, c, _f, _m in got if c.startswith("manual-")]
            self.assertEqual(manual, [],
                             "자모가 분해돼도 장부 파일이다 — manual 로 새면 같은 PDF 를 두 번 요약한다")

    def test_broken_meta_still_yields_manual(self):
        """meta.json 이 깨졌다고 손 업로드까지 사라지면 화면과 말이 달라진다."""
        with tempfile.TemporaryDirectory() as tmp:
            d = self._mk(tmp)
            (d / "meta.json").write_text("{깨짐", encoding="utf-8")
            got = sm._targets("2026-2", root=pathlib.Path(tmp), include_manual=True)
            names = sorted(f for _wd, _cid, f, _m in got)
            self.assertEqual(names, ["강의.pdf", "손PDF.pdf"],
                             "장부를 못 읽으면 materials/ 의 PDF 가 전부 장부 밖이다")
            self.assertEqual(got[0][3], {}, "meta 는 빈 dict 로 넘어간다")
```

- [ ] **Step 2: 테스트가 실패하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_summarize.TargetsManual -v
```

Expected: FAIL — `TypeError: _targets() got an unexpected keyword argument 'include_manual'`

- [ ] **Step 3: `_targets` 를 고친다**

`src/ssu_agent/summarize.py:261` 의 `_targets` 를 통째로 아래로 바꾼다. 파일 상단 `import` 블록(`import json / os / re / time`)에 `import unicodedata` 를 알파벳 순으로 끼워 넣는다.

```python
def _targets(semester, root=None, include_manual=False):
    """(주차디렉터리, content_id, 파일명, meta) — 받아둔 PDF.

    기본은 **장부(meta.json items)의 PDF 만**이다. 손으로 올린 파일은 장부
    밖에 일부러 두었고, 그게 03:00 cron 이 돈을 안 쓰는 이유다(자동 과금 0).
    `include_manual` 은 사람이 명시적으로 켤 때만 열린다 — 그때 `materials/`
    를 직접 훑어 장부 밖 `.pdf` 를 `manual-{파일명}` 키로 추가한다.
    LMS content_id 는 숫자라 이 접두사와 절대 안 겹친다.
    """
    base = (root or DATA_DIR) / semester
    out = []
    if not base.is_dir():
        return out
    for course in sorted(p for p in base.iterdir() if p.is_dir()):
        for wd in sorted(p for p in course.iterdir() if p.is_dir()):
            try:
                meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 장부를 못 읽으면 장부가 없는 것과 같다. include_manual 이
                # 꺼져 있으면 예전처럼 이 주차를 통째로 건너뛴다.
                if not include_manual:
                    continue
                meta = {}
            ledger = set()
            for cid, rec in sorted((meta.get("items") or {}).items()):
                f = rec.get("file") or ""
                if f:
                    ledger.add(unicodedata.normalize("NFC", f))
                if f.lower().endswith(".pdf") and (wd / "materials" / f).exists():
                    out.append((wd, cid, f, meta))
            if not include_manual:
                continue
            md = wd / "materials"
            if not md.is_dir():
                continue
            for p in sorted(md.iterdir()):
                n = p.name
                if n.startswith(".") or not p.is_file():
                    continue
                if not n.lower().endswith(".pdf"):
                    continue          # pptx·zip 은 범위 밖 (§2)
                if unicodedata.normalize("NFC", n) in ledger:
                    continue          # 장부 파일은 위에서 이미 넣었다
                out.append((wd, "manual-" + n, n, meta))
    return out
```

- [ ] **Step 4: 테스트가 통과하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_summarize -v
```

Expected: 새 `TargetsManual` 5개 PASS · 기존 `test_summarize.py` 전부 PASS

- [ ] **Step 5: 전체 스위트로 회귀를 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK (실패 0)

- [ ] **Step 6: 커밋**

```bash
cd ~/ssu-agent
git add src/ssu_agent/summarize.py tests/test_summarize.py
git commit -m "feat(summarize): _targets(include_manual) — 장부 밖 손 업로드 PDF 를 명시 호출에서만 걷는다"
```

---

### Task 2: `run()` · `estimate()` 로 플래그를 흘린다

**Files:**
- Modify: `~/ssu-agent/src/ssu_agent/summarize.py:319-320` (`run` 시그니처·`_targets` 호출), `:409-411` (`estimate` 시그니처·`_targets` 호출)
- Test: `~/ssu-agent/tests/test_summarize.py` (Task 1 에서 추가한 `TargetsManual` 아래)

**Interfaces:**
- Consumes: Task 1 의 `_targets(semester, root=None, include_manual=False)`
- Produces: `run(semester, extract=..., llm=..., chunk_size=..., max_calls=..., root=None, log=print, include_manual=False)` 와 `estimate(semester, extract=..., chunk_size=..., root=None, include_manual=False)`. Task 3 의 `cmd_summarize` 가 이 키워드로 부른다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_summarize.py` 의 `TargetsManual` 클래스 **아래**에 붙인다. 기존 `Run` 클래스의 `_run` 헬퍼와 같은 방식으로 `DATA_DIR` 을 패치한다.

```python
class RunManual(unittest.TestCase):
    """run()/estimate() 가 플래그를 _targets 까지 흘리는지 — 여기가 새면
    03:00 cron 이 손 업로드를 LLM 에 보낸다."""

    def _week(self, tmp):
        d = wk(tmp)
        (d / "materials" / "손PDF.pdf").write_bytes(b"%PDF-1.7")
        return d

    def _run(self, tmp, **kw):
        seen = []

        def llm(prompt, **k):
            seen.append(prompt)
            return "요약본"

        with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
            res = sm.run("2026-2",
                         extract=lambda path: ("가" * 2000, {"pages": 13}),
                         llm=llm, log=lambda *a: None, **kw)
        res["_calls"] = len(seen)
        return res

    def test_default_does_not_touch_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._week(tmp)
            res = self._run(tmp)
            self.assertEqual(res["done"], 1, "장부의 강의.pdf 하나만")
            self.assertEqual(sm.load_progress(d, "manual-손PDF.pdf"), {},
                             "손 업로드에는 장부 기록조차 안 생긴다")

    def test_include_manual_summarizes_and_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._week(tmp)
            res = self._run(tmp, include_manual=True)
            self.assertEqual(res["done"], 2, "장부 1 + 손 업로드 1")
            rec = sm.load_progress(d, "manual-손PDF.pdf")
            self.assertEqual(rec["status"], "done")
            self.assertEqual(rec["file"], "손PDF.pdf",
                             "대시보드가 rec.file 로 매칭한다 — 키 합성과 무관해야 한다")
            self.assertIn("## 손PDF.pdf", (d / "summary.md").read_text(encoding="utf-8"))

    def test_second_run_skips_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._week(tmp)
            self._run(tmp, include_manual=True)
            res = self._run(tmp, include_manual=True)
            self.assertEqual(res["done"], 0)
            self.assertEqual(res["skipped"], 2, "재개 장부가 손 업로드에도 그대로 걸린다")

    def test_estimate_respects_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._week(tmp)
            with mock.patch.object(sm, "DATA_DIR", pathlib.Path(tmp)):
                off = sm.estimate("2026-2",
                                  extract=lambda p: ("가" * 2000, {"pages": 13}))
                on = sm.estimate("2026-2",
                                 extract=lambda p: ("가" * 2000, {"pages": 13}),
                                 include_manual=True)
            self.assertEqual(off["docs"], 1)
            self.assertEqual(on["docs"], 2, "--estimate 로 손 업로드 비용을 먼저 볼 수 있어야 한다")
```

- [ ] **Step 2: 테스트가 실패하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_summarize.RunManual -v
```

Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'include_manual'`

- [ ] **Step 3: `run` 과 `estimate` 를 고친다**

`src/ssu_agent/summarize.py` 에서 두 군데만 바꾼다. 본문 로직은 손대지 않는다.

```python
def run(semester, extract=extract_pdf, llm=call_llm, chunk_size=CHUNK_CHARS,
        max_calls=MAX_CALLS, root=None, log=print, include_manual=False):
    res = {"done": 0, "skipped": 0, "failed": 0, "unsupported": 0,
           "calls": 0, "budget_hit": False, "in_tokens": 0, "out_tokens": 0}
    for wd, cid, fname, meta in _targets(semester, root, include_manual):
```

```python
def estimate(semester, extract=extract_pdf, chunk_size=CHUNK_CHARS, root=None,
             include_manual=False):
    """키 없이 도는 눈대중. 실제 청구서가 아니다."""
    e = {"docs": 0, "unsupported": 0, "skipped": 0, "chars": 0, "chunks": 0}
    for wd, cid, fname, _meta in _targets(semester, root, include_manual):
```

- [ ] **Step 4: 테스트가 통과하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK (실패 0) — `RunManual` 4개 포함

- [ ] **Step 5: 커밋**

```bash
cd ~/ssu-agent
git add src/ssu_agent/summarize.py tests/test_summarize.py
git commit -m "feat(summarize): run/estimate 에 include_manual 전달"
```

---

### Task 3: CLI — `--include-manual` + `_Args` 를 누락이 터지는 모양으로

**Files:**
- Modify: `~/ssu-agent/src/ssu_agent/cli.py` — 모듈 docstring `:11`, `cmd_summarize`(`:219-251`), `class _Args`(`:269-271`), `cmd_refresh` 의 summary 람다(`:320-321`), `summarize` 서브파서(`:377-384`)
- Test: `~/ssu-agent/tests/test_cli_summarize_args.py` (신규)

**Interfaces:**
- Consumes: Task 2 의 `run(..., include_manual=...)` · `estimate(..., include_manual=...)`
- Produces: `cli.SUMMARIZE_FIELDS = ("models", "estimate", "limit", "include_manual")` 와 `cli._Args(_fields=None, **kw)` — `_fields` 를 주면 누락 필드에서 `TypeError`. Task 6 의 스킬 문서가 `ssu-agent summarize --include-manual` 을 트리거로 적는다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

새 파일 `~/ssu-agent/tests/test_cli_summarize_args.py`:

```python
# -*- coding: utf-8 -*-
"""CLI — --include-manual 배선과 _Args 의 필드 누락 방어.

🔴 이 자리에서 두 번 밟았다. 2026-09-05 `--limit=None` 사고(요약 단계가 통째로
죽음)가 `_Args` 의 조용한 통과에서 났고, `include_manual` 은 같은 자리에서
이번엔 AttributeError 를 낼 뻔했다. 세 번째를 막는 게 이 테스트다.
"""
import unittest

from ssu_agent import cli


class ArgsShell(unittest.TestCase):
    def test_missing_field_raises(self):
        with self.assertRaises(TypeError):
            cli._Args(cli.SUMMARIZE_FIELDS, models=False, estimate=False, limit=5)

    def test_complete_field_set_is_ok(self):
        a = cli._Args(cli.SUMMARIZE_FIELDS, models=False, estimate=False,
                      limit=5, include_manual=False)
        self.assertEqual(a.limit, 5)
        self.assertFalse(a.include_manual)

    def test_without_fields_stays_permissive(self):
        """다른 호출부(fresh = _Args(refresh=..., dry_run=..., verbose=...))는
        그대로 돌아야 한다 — _fields 는 선택 인자다."""
        a = cli._Args(refresh=False, dry_run=True, verbose=False)
        self.assertTrue(a.dry_run)


class Parser(unittest.TestCase):
    def test_flag_defaults_off(self):
        a = cli.build_parser().parse_args(["summarize"])
        self.assertFalse(a.include_manual, "🔴 기본값이 켜지면 03:00 cron 이 돈을 쓴다")

    def test_flag_on(self):
        a = cli.build_parser().parse_args(["summarize", "--include-manual"])
        self.assertTrue(a.include_manual)


class RefreshDoesNotOptIn(unittest.TestCase):
    """refresh 는 코코봇 입구다. 여기서 손 업로드가 새면 '최신 LMS 업데이트해줘'
    한 마디가 예고 없이 손 업로드 요약비까지 쓴다."""

    def test_summary_step_passes_include_manual_false(self):
        seen = {}

        def fake_summarize(a):
            seen["include_manual"] = a.include_manual
            seen["limit"] = a.limit
            print("요약 0 · 건너뜀 0")
            return 0

        def fake_refresh_run(fns, want=()):
            fns["summary"]()
            return {"steps": [], "aborted": False}

        orig_cmd, orig_run = cli.cmd_summarize, cli.refresh.run
        cli.cmd_summarize = fake_summarize
        cli.refresh.run = fake_refresh_run
        try:
            cli.cmd_refresh(cli._Args(no_summary=False, no_materials=False,
                                      dry_run=False, limit=7, verbose=False))
        finally:
            cli.cmd_summarize, cli.refresh.run = orig_cmd, orig_run
        self.assertEqual(seen, {"include_manual": False, "limit": 7})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest tests.test_cli_summarize_args -v
```

Expected: FAIL — `AttributeError: module 'ssu_agent.cli' has no attribute 'SUMMARIZE_FIELDS'`

- [ ] **Step 3: `cli.py` 를 고친다 (5곳)**

**(1)** 모듈 docstring `:11` 의 `summarize` 줄을 바꾼다:

```
    ssu-agent summarize           자료 → 마크다운 → LLM 요약 (--estimate 로 비용 먼저)
                                  --include-manual 로 손 업로드 PDF 까지 (자동은 안 본다)
```

**(2)** `class _Args` 를 바꾸고 바로 위에 필드 선언을 둔다 (`:269` 부근):

```python
# cmd_summarize 가 읽는 필드. _Args 로 껍데기를 만들 때 이 목록을 넘기면
# 🔴 필드를 빠뜨린 자리에서 **즉시** TypeError 로 터진다.
# 2026-09-05 에 여기서 두 번 밟았다 — limit=None 은 요약 단계를 통째로
# 죽였고(조용한 None 전파), include_manual 은 AttributeError 를 낼 뻔했다.
SUMMARIZE_FIELDS = ("models", "estimate", "limit", "include_manual")


class _Args(object):
    def __init__(self, _fields=None, **kw):
        if _fields:
            missing = [f for f in _fields if f not in kw]
            if missing:
                raise TypeError("_Args 필드 누락: %s" % ", ".join(missing))
        self.__dict__.update(kw)
```

**(3)** `cmd_refresh` 의 summary 람다(`:320`)를 바꾼다. 🔴 `include_manual=False` 는 **자동 경로가 손 업로드를 영원히 안 본다**는 계약이다:

```python
        # 🔴 dry-run 은 estimate 로 간다. 안 그러면 "확인만 할게" 가 돈을 쓴다.
        # 🔴 include_manual=False 고정 — refresh 는 코코봇이 한 마디로 부르는
        #    입구다. 손 업로드 요약은 사람이 --include-manual 로 명시할 때만.
        "summary": lambda: _quiet(cmd_summarize, _Args(
            SUMMARIZE_FIELDS, models=False, estimate=a.dry_run, limit=a.limit,
            include_manual=False)),
```

**(4)** `cmd_summarize`(`:232`, `:242`)에서 두 호출에 플래그를 넘긴다:

```python
    if a.estimate:
        e = summarize.estimate(sem, include_manual=a.include_manual)
```

```python
    try:
        res = summarize.run(sem, max_calls=a.limit,
                            include_manual=a.include_manual)
```

**(5)** `summarize` 서브파서(`:377` 부근)에 플래그를 추가한다:

```python
    su.add_argument("--include-manual", action="store_true",
                    help="대시보드로 손수 올린 PDF 도 요약 (기본 꺼짐 — 자동 경로는 안 본다)")
```

- [ ] **Step 4: 테스트가 통과하는 걸 확인한다**

```bash
cd ~/ssu-agent && python3 -m unittest discover -s tests -t .
```

Expected: OK (실패 0)

- [ ] **Step 5: CLI 가 실제로 뜨는지 본다 (돈 안 씀)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent summarize --help
```

Expected: `--include-manual` 이 도움말에 보인다

- [ ] **Step 6: 커밋**

```bash
cd ~/ssu-agent
git add src/ssu_agent/cli.py tests/test_cli_summarize_args.py
git commit -m "feat(cli): summarize --include-manual · _Args 는 필드 누락에서 터진다"
```

---

### Task 4: 대시보드 — 손 업로드도 `.progress` 를 먼저 본다

**Files:**
- Modify: `~/eunzi-os-dashboard/lib/study.js:97-120` (`listMaterials` 의 manual 분기)
- Test: `~/eunzi-os-dashboard/test/study.test.cjs` (기존 "손으로 넣은 파일" 블록 아래)

**Interfaces:**
- Consumes: Task 1~3 이 쓰는 `.progress/manual-{파일명}.json` (레코드의 `file` 필드가 실제 파일명 — `summaryStatuses()` 가 그걸로 매칭하므로 **키 합성 방식과 무관하다**)
- Produces: `readWeek(...).materials[]` 의 각 항목이 `{ name, size, viewable, source: "lms"|"manual", summary, summaryNote }`. 손 업로드는 `.progress` 기록이 있으면 그 상태(`done`/`failed`/`in_progress`/`unsupported_scanned`), 없으면 PDF 는 `"manual"`, PDF 아니면 `"unsupported_manual"`. Task 5 의 `MaterialTabs.tsx` 가 이 값으로 배지를 고른다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`~/eunzi-os-dashboard/test/study.test.cjs` 의 "손으로 넣은 파일" 블록 **아래**에 붙인다. 파일 상단의 `sandbox()` 헬퍼(장부에 `강의노트.pdf` 하나)를 그대로 쓴다.

```js
// ── 손 업로드도 요약될 수 있다 (ssu-agent summarize --include-manual).
//    .progress 를 먼저 보고, 기록이 없을 때만 "미요약"이다.
{
  const { root, w } = sandbox();
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  fs.mkdirSync(path.join(w, ".progress"), { recursive: true });
  fs.writeFileSync(path.join(w, ".progress", "manual-손PDF.pdf.json"), JSON.stringify({
    file: "손PDF.pdf", status: "done", model: "claude-opus-5",
  }), "utf8");
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  const m = Object.fromEntries(got.materials.map((x) => [x.name, x]));
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
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  const m = Object.fromEntries(got.materials.map((x) => [x.name, x]));
  assert.strictEqual(m["손PDF.pdf"].summary, "failed");
  assert.strictEqual(m["손PDF.pdf"].summaryNote, "429 rate limit",
    "손 업로드도 실패 사유를 보여준다");
}
{
  const { root, w } = sandbox();     // 기록이 없는 손 PDF
  fs.writeFileSync(path.join(w, "materials", "손PDF.pdf"), "%PDF-1.7 x");
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  const m = Object.fromEntries(got.materials.map((x) => [x.name, x]));
  assert.strictEqual(m["손PDF.pdf"].summary, "manual", "아직 안 시킨 것");
  assert.strictEqual(m["손PDF.pdf"].summaryNote, null);
}
{
  const { root, w } = sandbox();     // PDF 아닌 손 업로드는 요약 자체가 불가
  fs.writeFileSync(path.join(w, "materials", "교재.pptx"), "PK\x03\x04");
  fs.writeFileSync(path.join(w, "materials", "묶음.zip"), "PK\x03\x04");
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  const m = Object.fromEntries(got.materials.map((x) => [x.name, x]));
  assert.strictEqual(m["교재.pptx"].summary, "unsupported_manual",
    "pymupdf4llm 이 못 읽는다 — 시켜도 안 되는 걸 '미요약'이라 하면 안 된다");
  assert.strictEqual(m["묶음.zip"].summary, "unsupported_manual");
}
{
  const { root, w } = sandbox();     // 장부 파일은 아무것도 안 바뀐다
  fs.mkdirSync(path.join(w, ".progress"), { recursive: true });
  fs.writeFileSync(path.join(w, ".progress", "abc.json"), JSON.stringify({
    file: "강의노트.pdf", status: "done",
  }), "utf8");
  const got = study.readWeek("2026-2", "선형대수", 3, root);
  assert.strictEqual(got.materials[0].source, "lms");
  assert.strictEqual(got.materials[0].summary, "done");
}
```

- [ ] **Step 2: 테스트가 실패하는 걸 확인한다**

```bash
source ~/.nvm/nvm.sh && nvm use 20 && cd ~/eunzi-os-dashboard && node test/study.test.cjs
```

Expected: FAIL — `AssertionError: 요약됐으면 요약됐다고 말해야 한다 ... 'manual' !== 'done'`

- [ ] **Step 3: `listMaterials` 의 manual 분기를 고친다**

`~/eunzi-os-dashboard/lib/study.js` 의 `listMaterials` 안, `if (!ledger[name.normalize("NFC")]) { ... }` 블록을 바꾼다. 그 위 `const st = summaryStatuses(dir);` 는 그대로 쓴다 (`summaryStatuses` 는 `rec.file` 로 매칭하므로 `manual-` 키에도 그대로 붙는다 — **손대지 않는다**).

```js
    // 장부(meta.json items)에 없으면 손으로 넣은 파일이다. 03:00 cron 은 이
    // 파일을 영원히 안 본다 — 하지만 사람이 `summarize --include-manual` 로
    // 시킬 수 있으므로, .progress 에 기록이 있으면 그게 진실이다.
    // NFC 정규화 후 비교 — ledgerFiles 가 NFC 로 저장하므로 여기도 맞춘다.
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

- [ ] **Step 4: 테스트가 통과하는 걸 확인한다**

```bash
source ~/.nvm/nvm.sh && nvm use 20 && cd ~/eunzi-os-dashboard && npm test
```

Expected: 6개 스위트 모두 통과 (`study.test.cjs — 통과` 포함)

- [ ] **Step 5: 커밋**

```bash
cd ~/eunzi-os-dashboard
git add lib/study.js test/study.test.cjs
git commit -m "feat(study): 손 업로드도 .progress 를 먼저 본다 · 비PDF 는 unsupported_manual"
```

---

### Task 5: 화면 문구 — "직접 올림" → "미요약", 시키는 법 한 줄

**Files:**
- Modify: `~/eunzi-os-dashboard/app/components/MaterialTabs.tsx:14-43` (`SUM_LABEL`, `why()`)

**Interfaces:**
- Consumes: Task 4 가 만드는 `summary` 값 — `"manual"` · `"unsupported_manual"` 및 손 업로드의 `"done"`/`"failed"`/`"in_progress"`/`"unsupported_scanned"`
- Produces: 없음 (표시 계층 끝)

- [ ] **Step 1: `SUM_LABEL` 을 고친다**

```tsx
// 요약이 없을 때 "왜"에 따라 사람이 할 일이 다르다
const SUM_LABEL: Record<string, [string, string]> = {
  done: ["요약됨", "ok"],
  unsupported_scanned: ["스캔 PDF", "warn"],
  failed: ["요약 실패", "bad"],
  in_progress: ["요약 중단", "warn"],
  pending: ["요약 대기", "muted"],
  // 손으로 올린 PDF — 03:00 cron 은 안 보지만 사람이 시키면 요약된다.
  // "직접 올림"은 출처를 말할 뿐이라 다음에 뭘 할 수 있는지를 못 알려줬다.
  manual: ["미요약", "muted"],
  unsupported_manual: ["요약 미지원", "muted"],
};
```

- [ ] **Step 2: `why()` 의 전부-손업로드 분기에 시키는 법을 붙인다**

`why()` 안의 `if (candidates.length === 0)` 분기 return 문만 바꾼다. 나머지 로직(`candidates` 가 자동 요약 대상만 세는 것)은 그대로 둔다 — 자동 경로는 여전히 손 업로드를 안 보기 때문이다.

```tsx
  if (candidates.length === 0) {
    // 자료는 있지만 전부 손으로 올린 파일 — "받아둔 자료가 없습니다"는
    // 자료 탭에 목록이 뜨는 상황과 모순되는 거짓말이라 따로 문구를 둔다.
    return "이 주차 자료는 전부 손으로 올린 파일입니다. LMS 장부 밖이라 자동 요약 대상이 아닙니다. "
      + "터미널에서 ssu-agent summarize --include-manual 로 시키면 요약됩니다 (PDF 만, 비용 발생).";
  }
```

- [ ] **Step 3: 타입과 빌드를 확인한다**

```bash
source ~/.nvm/nvm.sh && nvm use 20 && cd ~/eunzi-os-dashboard && npx tsc --noEmit && npm run build
```

Expected: tsc 에러 0 · 빌드 성공 (9/9 페이지)

- [ ] **Step 4: 커밋**

```bash
cd ~/eunzi-os-dashboard
git add app/components/MaterialTabs.tsx
git commit -m "feat(univ): 손 업로드 배지를 '미요약'으로 · 시키는 법을 화면에 적는다"
```

---

### Task 6: 코코봇 스킬 — 트리거와 함정

**Files:**
- Modify: `~/.hermes/skills/eunzi/univ-save/SKILL.md` (frontmatter `version`, "LMS 동기화" 절)

**Interfaces:**
- Consumes: Task 3 의 `ssu-agent summarize --include-manual`
- Produces: 없음 (문서)

- [ ] **Step 1: frontmatter 버전을 올린다**

`version: 1.1.0` → `version: 1.2.0`. 🔴 **`description` 은 건드리지 않는다** — 57자 규칙(`skill_utils.py:624` 가 `desc[:57] + "..."` 로 자른다)에 이미 맞춰져 있고, 이번 변경은 표 안에서만 일어난다.

- [ ] **Step 2: "LMS 동기화" 절의 표 아래에 손 업로드 행과 함정을 붙인다**

기존 표 3행(`refresh` / `--no-summary` / `--dry-run`) **바로 아래**에 넣는다:

```markdown
### 손으로 올린 자료 요약 (2026-09-05 신설)

| 은지가 하는 말 | 명령 |
|---|---|
| "내가 올린 자료도 요약해줘" · "직접 올린 PDF 요약해줘" | `~/ssu-agent/bin/ssu-agent summarize --include-manual` |
| "얼마 나올지부터 보자" | `... summarize --include-manual --estimate` |

대시보드로 손수 올린 PDF 는 **LMS 장부 밖**이라 자동 경로가 영원히 안 본다.
`--include-manual` 은 그걸 **이번 실행에서만** 연다.

- 🔴 **`refresh` 에는 안 들어간다.** "최신 LMS 업데이트해줘" 로는 손 업로드가
  요약되지 않는다 — 예고 없이 돈을 쓰지 않으려고 **일부러** 뺐다. 은지가 손
  업로드를 콕 집어 말할 때만 이 명령이다
- 🔴 **돈을 쓴다** (Claude API). 애매하면 `--estimate` 를 먼저 돌려 예상 비용을
  보여주고 물어라
- **PDF 만 된다.** ppt/pptx/zip 은 `요약 미지원` 이다 — 안 되는 걸 되는 것처럼
  말하지 마라
- `timeout=600` 을 명시해라. 자료가 크면 몇 분 걸린다
```

- [ ] **Step 3: 스킬이 깨지지 않았는지 확인한다**

```bash
bash ~/eunzi-tools/bin/doctor.sh; echo "exit=$?"
```

Expected: `exit=0` (설계서 §4 대조 경고가 뜨면 Task 8 에서 문서를 갱신한 뒤 다시 돌린다)

- [ ] **Step 4: 커밋 — `~/.hermes/skills` 는 git repo 가 아니면 건너뛴다**

```bash
cd ~/.hermes/skills/eunzi/univ-save && git rev-parse --is-inside-work-tree 2>/dev/null \
  && git add SKILL.md && git commit -m "univ-save v1.2.0 — 손 업로드 요약 트리거" \
  || echo "git repo 아님 — 파일만 갱신 (정본은 여기다)"
```

---

### Task 7: 실측 — PDF 2건을 실제로 요약한다

**Files:** 없음 (실행·확인만)

**Interfaces:**
- Consumes: Task 1~5 전부
- Produces: `~/ssu-agent/data/2026-2/{과목}/W01/summary.md` 의 손 업로드 섹션 · `.progress/manual-*.json`

**대상 (설계 §4 실측):** 선형대수 W01 `Chapter 01. 선형대수학의 개요.pdf`(3.2MB) · 확장현실디자인 W01 `1.Oculus_Link_to_PC.pdf`(7.0MB). 같은 폴더의 `Chapter 00. 교재 소개.pptx` 는 대상이 아니어야 한다.

- [ ] **Step 1: 대상이 맞는지 눈으로 본다 (돈 안 씀)**

```bash
cd ~/ssu-agent && python3 -c "
from ssu_agent import summarize as sm
from ssu_agent.config import get
sem = get().semester
off = {f for _w,_c,f,_m in sm._targets(sem)}
on  = {f for _w,_c,f,_m in sm._targets(sem, include_manual=True)}
print('플래그로 새로 들어온 것:')
for f in sorted(on - off): print('  +', f)
print('pptx 가 있으면 버그:', [f for f in on if not f.lower().endswith('.pdf')])
"
```

Expected: `+ Chapter 01. 선형대수학의 개요.pdf`, `+ 1.Oculus_Link_to_PC.pdf` 두 줄. pptx 목록은 `[]`

- [ ] **Step 2: 비용을 먼저 어림한다 (LLM 호출 없음)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent summarize --include-manual --estimate
```

Expected: 문서 2개(이미 요약된 LMS 자료는 `skipped`)와 예상 `$`. **$1 을 넘으면 멈추고 은지에게 보고한다.**

- [ ] **Step 3: 실제로 돌린다 (🔴 여기서 돈이 나간다)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent summarize --include-manual --limit 8
```

Expected: `요약 2 · 건너뜀 N · 스캔 0 · 실패 0 · 호출 M회` + 실제 토큰 줄

- [ ] **Step 4: 결과가 실제로 남았는지 본다**

```bash
cd ~/ssu-agent
grep -c "^## " "data/2026-2/선형대수/W01/summary.md"
ls data/2026-2/선형대수/W01/.progress/ | grep manual
python3 -c "
import json,glob
for p in glob.glob('data/2026-2/*/W01/.progress/manual-*.json'):
    r=json.load(open(p)); print(r['status'], '·', r['file'])
"
```

Expected: `summary.md` 에 손 업로드 파일명 섹션이 있고, `manual-*.json` 의 `status` 가 `done`, `file` 이 실제 파일명

- [ ] **Step 5: 자동 경로가 여전히 안 보는지 확인한다 (계약 검증)**

```bash
cd ~/ssu-agent && ./bin/ssu-agent refresh --dry-run
```

Expected: 요약 단계가 `estimate` 로 가고, 그 문서 수가 **손 업로드를 포함하지 않는다**. (Step 2 의 `--include-manual --estimate` 보다 문서 수가 적거나 같다.)

- [ ] **Step 6: 대시보드에서 눈으로 본다**

```bash
open "http://localhost:3001/univ/선형대수/1"
```

Expected: 손 업로드 PDF 배지가 **「요약됨」**, pptx 는 **「요약 미지원」**, 「📝 강의자료 요약」 탭에 새 섹션이 보인다. (3001 이 안 뜨면 `pm2 list` 로 확인 — 빌드·재시작은 nvm Node 20 PATH 필요)

- [ ] **Step 7: 실측 결과를 기록한다**

Task 8 의 History 문서에 넣을 숫자를 적어둔다 — 문서 2건의 **실제 입력·출력 토큰과 청구 예상액**, 걸린 시간, `--estimate` 어림과의 차이.

---

### Task 8: 문서 갱신 (CLAUDE.md §0 — 이걸 해야 끝난 것이다)

**Files:**
- Modify: `~/ssu-agent/README.md` · `~/eunzi-os/05_AI/프로젝트.md` · `~/eunzi-os/05_AI/가이드.md` · `~/eunzi-os/05_AI/시스템/05_스킬 기능표.md` · `~/eunzi-os/05_AI/시스템/01_에르메스 설계.md` §4 · `~/eunzi-os/HISTORY.md`
- Create: `~/eunzi-os/05_AI/시스템/History/2026-09-05_ssu-agent_손업로드요약.md`
- Move: `~/eunzi-os/05_AI/작업중/2026-09-05_손업로드요약_설계.md` (완료 처리)

**Interfaces:**
- Consumes: Task 7 의 실측 숫자
- Produces: 없음 (마지막)

- [ ] **Step 1: `ssu-agent/README.md`**

`## 테스트` 절 위(명령 소개 부근)에 `summarize --include-manual` 한 줄을 넣고, 배치 표의 `com.eunzi.ssu-materials` 행 아래에 **자동 경로는 손 업로드를 안 본다**고 적는다. 🔴 「아직 안 한 것」 목록에는 손 업로드 요약 항목이 **없다** — 회수할 줄이 없으니 지어내지 말고 사용법에 추가만 한다.

- [ ] **Step 2: `05_AI/프로젝트.md`**

「대학 · v2 · ssu-agent」 `active` 절(설계 §6 판정 Ⓐ)에 완료 한 줄. 🔴 착수 전에 후보 줄이 **대학 v2 절에** 있는지 확인한다 (2026-09-05 에 eunzi-os v3 절에 잘못 넣었다가 옮긴 적 있다).

- [ ] **Step 3: `05_AI/가이드.md`**

트리거 한 줄 — "내가 올린 자료도 요약해줘" → `ssu-agent summarize --include-manual` (PDF 만, 돈 씀, `refresh` 에는 안 들어간다).

- [ ] **Step 4: `05_AI/시스템/05_스킬 기능표.md`**

`univ-save` 항목에 손 업로드 요약 트리거 추가. 🔴 **§2 대조표 숫자까지** 맞춘다.

- [ ] **Step 5: `05_AI/시스템/01_에르메스 설계.md` §4**

`univ-save` 행의 기능·버전(v1.2.0) 갱신.

- [ ] **Step 6: History 상세 + `HISTORY.md` 한 줄**

`~/eunzi-os/05_AI/시스템/History/2026-09-05_ssu-agent_손업로드요약.md` 를 만든다. frontmatter 에 🔴 `component: hermes` 필수(스킬·에이전트 변경). 본문에 남길 것: **왜 「일부러 안 함」을 뒤집었나**, 방화벽을 장부→플래그로 옮긴 구조, `_Args` (b) 결정과 그 자리에서 두 번 밟은 이력, Task 7 실측 숫자(토큰·비용·시간).

```bash
cd ~/eunzi-os && python3 ~/eunzi-tools/bin/history_index.py --write
```

`HISTORY.md` 에 날짜별 한 줄도 추가한다.

- [ ] **Step 7: 설계 노트를 작업중에서 뺀다**

`05_AI/작업중/2026-09-05_손업로드요약_설계.md` 의 frontmatter `status` 를 완료로 바꾸고 History 문서를 가리키게 한다 (`tags` 의 `todo` 제거). **해소한 것은 지운다** — §5 "은지가 정하면 착수한다" 같은 미결 문구가 남으면 다음 세션이 또 멈춘다.

- [ ] **Step 8: 기계로 검증한다**

```bash
bash ~/eunzi-tools/bin/doctor.sh; echo "exit=$?"
```

Expected: `exit=0`. **설계서↔실제 대조가 여기서 잡힌다 — 손으로 대조하지 마라.**

- [ ] **Step 9: 커밋 (repo 3개, 따로)**

```bash
cd ~/ssu-agent && git add README.md docs/superpowers/plans/2026-09-05-manual-upload-summarize.md \
  && git commit -m "docs: 손 업로드 요약 — 계획·사용법"
cd ~/eunzi-os && git add "05_AI/프로젝트.md" "05_AI/가이드.md" \
  "05_AI/시스템/05_스킬 기능표.md" "05_AI/시스템/01_에르메스 설계.md" \
  "05_AI/시스템/History/2026-09-05_ssu-agent_손업로드요약.md" \
  "05_AI/시스템/History/History.md" HISTORY.md \
  "05_AI/작업중/2026-09-05_손업로드요약_설계.md" \
  && git commit -m "손 업로드 자료 요약 — 문서 갱신"
```

🔴 vault 커밋은 **iMac 에서만**. `git add -A` 금지.
