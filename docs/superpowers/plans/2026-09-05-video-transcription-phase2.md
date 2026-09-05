# 동영상 전사 Phase 2 — 예산·장부·잠금

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전사를 **무인으로 밤새 돌려도 안전하게** 만든다 — 시간 상한에 걸리면 다음 새벽이 이어받고, 크론과 수동이 겹쳐도 같은 파일을 둘이 건드리지 않으며, 실패한 mp4 가 디스크를 무한정 먹지 않는다.

**Architecture:** Phase 1 의 `run_one`(영상 하나)은 그대로 두고, 그 위에 **`run_batch`(여러 개 + 예산)** 를 얹는다. 장부(`.progress/`)와 상한 처리는 `summarize.py` 가 이미 쓰는 규약을 그대로 따른다 — 새 패턴을 만들지 않는다. 잠금은 `cmd_transcribe` 한 곳에서만 건다.

**Tech Stack:** Python 3.9 stdlib (`fcntl.flock`) · unittest

**Spec:** `docs/superpowers/specs/2026-09-05-video-transcription-design.md` (§5 예산 · §6 재개와 실패 · §8.4 잠금)

**이전 단계:** `docs/superpowers/plans/2026-09-05-video-transcription-phase1.md` (T1~T5 완료, 테스트 194건)

## Global Constraints

- **의존성 0 규약** — `faster_whisper` 는 **함수 안에서만** import. 이 계획은 무거운 import 를 새로 만들지 않는다.
- **테스트는 네트워크·모델을 타지 않는다.** `run_batch` 는 `run_one` 을 주입받아 테스트한다.
- **테스트 실행** — `python3 -m unittest discover -s tests -t . -q` (pytest 없음). 시작 시점 **194건**.
- **장부 위치** — `data/{학기}/{과목}/W{주차}/.progress/{content_id}.json`. `summarize.progress_dir`·`load_progress`·`save_progress` 를 **재사용한다**(같은 폴더·같은 파일명 규칙). 🔴 새로 만들지 마라.
- **status 값** — `done` / `failed` / `in_progress` 세 가지만 (스펙 §6). `summarize` 의 `unsupported_scanned` 는 전사에 해당 없음.
- **환경변수 기본값** — `TRANSCRIBE_MAX_SECONDS` = `10800`(3시간) · `TRANSCRIBE_TMP_MAX_BYTES` = `2147483648`(2GB). 둘 다 스펙 §5 의 값 그대로.
- **잠금 파일** — `state/transcribe.lock`. `state/` 는 git 미추적.
- **커밋 규약** — 태스크마다 1커밋. 첫 줄에 `[P2-T{번호}]`. 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` (이 저장소 관례).
- **🔴 실측 기준 숫자** — 백로그 **18.3시간**(영상 37개), `small` **3.06× 실시간** → 전사 **6.0시간**. 3시간 창 **2일**. (Phase 1 「실측 결과」. 설계 초안의 "36시간" 은 틀린 값이었고 2026-09-05 정정됐다.)

## 진행 상황 (세션이 끊기면 여기부터)

- [ ] **P2-T1 장부** — `mark`/`is_done`, `run_one` 이 장부를 쓴다
- [ ] **P2-T2 예산** — `run_batch` + `TRANSCRIBE_MAX_SECONDS`
- [ ] **P2-T3 디스크** — `sweep_tmp` + 실패 시 mp4 보존
- [ ] **P2-T4 잠금** — `flock` + CLI 배선

`git log --oneline | grep '\[P2-T'` 로 어디까지 했는지 확인한다.

---

### Task 1: 장부 — 무엇을 했고 무엇이 실패했나

Phase 1 의 `run_one` 은 **파일이 있으면 건너뛴다**(`md_path.exists()`). 그것만으로는 실패를 기억하지 못해서, 죽은 영상을 매번 다시 받아 다시 실패한다. 장부가 필요한 이유다.

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `run_one(job, semester, root=None, tmp_dir=None, resolve=None, fetch=None, transcriber=None, model=None, log=print) -> dict` (Phase 1)
- Consumes: `summarize.progress_dir(week_dir)` · `summarize.load_progress(week_dir, content_id) -> dict` · `summarize.save_progress(week_dir, content_id, rec) -> dict`
- Produces: `is_done(wd, content_id, md_path) -> bool` — 장부가 `done` 이고 파일도 실제로 있어야 True
- Produces: `run_one` 이 성공하면 `status="done"`, 예외가 나면 `status="failed"` + `last_error` 를 남기고 **예외를 삼킨다**(반환 `ok=False`)

🔴 **`materials.py` 의 규약을 따른다** — 장부에 있어도 **파일이 실제로 있어야** 건너뛴다(`materials.py:222-226`). `data/` 가 gitignore 라 clone 한 기기엔 둘 다 없고, 수동 정리로 파일만 사라지는 경우가 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_transcribe.py` 끝(`if __name__` 앞)에 넣는다.

```python
class TestIsDone(unittest.TestCase):
    def test_needs_both_ledger_and_file(self):
        from ssu_agent import summarize
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            md = wd / "markdown" / "x.md"
            self.assertFalse(transcribe.is_done(wd, "c1", md))   # 둘 다 없다

            summarize.save_progress(wd, "c1", {"status": "done"})
            self.assertFalse(transcribe.is_done(wd, "c1", md))   # 장부만 있다

            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text("x", encoding="utf-8")
            self.assertTrue(transcribe.is_done(wd, "c1", md))    # 둘 다 있다

    def test_failed_is_not_done(self):
        from ssu_agent import summarize
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            md = wd / "markdown" / "x.md"
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text("x", encoding="utf-8")
            summarize.save_progress(wd, "c1", {"status": "failed"})
            self.assertFalse(transcribe.is_done(wd, "c1", md))


class TestRunOneWritesLedger(unittest.TestCase):
    def _job(self):
        return {"stem": "선형대수", "week": 1, "content_id": "c1",
                "title": "선대 1-1", "duration": 60, "source": "s"}

    def test_success_marks_done(self):
        from ssu_agent import summarize
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            res = transcribe.run_one(
                self._job(), "2026-2", root=root,
                resolve=lambda cid: "http://x/v.mp4",
                fetch=lambda u, p: open(p, "wb").write(b"x"),
                transcriber=lambda p, initial_prompt=None: [
                    {"start": 0, "end": 1, "text": "가"}],
                log=lambda *a: None)
            self.assertTrue(res["ok"])
            wd = root / "2026-2" / "선형대수" / "W01"
            self.assertEqual(summarize.load_progress(wd, "c1")["status"], "done")

    def test_failure_marks_failed_and_does_not_raise(self):
        """한 영상의 실패가 다음 영상을 막지 않는다 (스펙 §6)."""
        from ssu_agent import summarize

        def boom(p, initial_prompt=None):
            raise RuntimeError("디코딩 실패")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            res = transcribe.run_one(
                self._job(), "2026-2", root=root,
                resolve=lambda cid: "http://x/v.mp4",
                fetch=lambda u, p: open(p, "wb").write(b"x"),
                transcriber=boom, log=lambda *a: None)
            self.assertFalse(res["ok"])
            wd = root / "2026-2" / "선형대수" / "W01"
            pr = summarize.load_progress(wd, "c1")
            self.assertEqual(pr["status"], "failed")
            self.assertIn("디코딩 실패", pr["last_error"])

    def test_done_ledger_skips_without_fetching(self):
        """장부가 done 이면 다운로드조차 안 한다."""
        touched = []
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = self._job()
            for _ in range(2):
                transcribe.run_one(
                    job, "2026-2", root=root,
                    resolve=lambda cid: "http://x/v.mp4",
                    fetch=lambda u, p: (touched.append(u),
                                        open(p, "wb").write(b"x"))[1],
                    transcriber=lambda p, initial_prompt=None: [
                        {"start": 0, "end": 1, "text": "가"}],
                    log=lambda *a: None)
        self.assertEqual(len(touched), 1, "두 번째 실행이 다시 받았다")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `module 'ssu_agent.transcribe' has no attribute 'is_done'`

- [ ] **Step 3: 구현한다**

`src/ssu_agent/transcribe.py` 의 import 절에 추가:

```python
from . import materials, summarize
```

(기존 `from . import materials` 를 위 줄로 바꾼다. `summarize` 는 stdlib 만 쓰는 모듈이라 "의존성 0" 규약을 깨지 않는다 — `pymupdf4llm` 도 함수 안에서만 부른다.)

`run_one` 바로 앞에 넣는다:

```python
def is_done(wd, content_id, md_path):
    """장부가 done 이고 **파일도 실제로 있어야** 건너뛴다.

    `materials.py` 와 같은 규약이다 — `data/` 가 gitignore 라 clone 한 기기엔
    둘 다 없고, 수동 정리로 파일만 사라지는 경우가 있다.
    """
    if summarize.load_progress(wd, content_id).get("status") != "done":
        return False
    return Path(md_path).exists()
```

`run_one` 안의 건너뛰기 판정과 본문을 아래로 바꾼다. 🔴 **기존 `if md_path.exists():` 블록을 통째로 대체한다.**

```python
    if is_done(wd, job["content_id"], md_path):
        log("  ⤼ %s — 이미 전사됨" % stem)
        return {"ok": True, "md_path": str(md_path), "seconds": 0.0,
                "chars": len(md_path.read_text(encoding="utf-8"))}
```

그리고 `fetch`~`_update_meta` 구간을 `try` 로 감싼다. 기존:

```python
    t0 = time.time()
    fetch(url, str(mp4))
    segs = transcriber(str(mp4), initial_prompt=build_prompt(job))
    took = time.time() - t0
```

를 이렇게:

```python
    summarize.save_progress(wd, job["content_id"],
                            {"file": md_path.name, "status": "in_progress"})
    t0 = time.time()
    try:
        fetch(url, str(mp4))
        segs = transcriber(str(mp4), initial_prompt=build_prompt(job))
    except Exception as e:          # 한 영상의 실패가 다음 영상을 막지 않는다
        log("  ✖ %s — %s" % (stem, e))
        summarize.save_progress(wd, job["content_id"],
                                {"file": md_path.name, "status": "failed",
                                 "last_error": str(e)[:300]})
        return {"ok": False, "md_path": None, "seconds": time.time() - t0,
                "chars": 0}
    took = time.time() - t0
```

마지막으로 `_update_meta(wd, job, md_path.name)` **다음 줄**에 추가:

```python
    summarize.save_progress(wd, job["content_id"],
                            {"file": md_path.name, "status": "done",
                             "model": model or MODEL, "seconds": round(took, 1)})
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK (194 + 5 = **199건**)

- [ ] **Step 5: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py
git commit -m "[P2-T1] 동영상 전사 — 장부 (done/failed/in_progress)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 예산 — 시간 상한과 이어받기

밤새 도는 잡이 낮까지 물고 있으면 안 된다. **파일 사이에서** 검사한다 — 전사 중간에 끊으면 그 파일은 어차피 처음부터라(재개 단위는 파일, 스펙 §6) 끊어봐야 손해만 본다.

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `run_one(...) -> {"ok": bool, "md_path": str|None, "seconds": float, "chars": int}` (Task 1 이후)
- Produces: `MAX_SECONDS` — 모듈 상수, `TRANSCRIBE_MAX_SECONDS` 환경변수, 기본 `10800`
- Produces: `run_batch(jobs, semester, limit=0, max_seconds=None, tmp_dir=None, runner=None, clock=None, log=print) -> dict`
  - 반환 `{"done": int, "failed": int, "skipped": int, "budget_hit": bool, "seconds": float}`
  - `runner(job) -> dict` 를 주입받는다 — 테스트가 `run_one` 없이 돈다
  - `clock() -> float` 를 주입받는다 — 테스트가 `time.sleep` 없이 시간을 흐르게 한다

🔴 **`summarize.run` 과 같은 모양으로 만든다** — `res["budget_hit"] = True; break`. 코코봇·`status` 가 나중에 두 잡을 같은 방식으로 읽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class TestRunBatch(unittest.TestCase):
    def _jobs(self, n):
        return [{"stem": "과목", "week": 1, "content_id": "c%d" % i,
                 "title": "t%d" % i, "duration": 600, "source": "s"}
                for i in range(n)]

    def test_counts_done_and_failed(self):
        def runner(job):
            return {"ok": job["content_id"] != "c1", "md_path": None,
                    "seconds": 1.0, "chars": 10}

        # 🔴 tmp_dir 를 항상 넘긴다 — T3 에서 run_batch 가 여기를 쓸게 된다.
        # 안 넘기면 테스트가 실제 state/tmp 를 청소한다.
        with tempfile.TemporaryDirectory() as d:
            res = transcribe.run_batch(self._jobs(3), "2026-2", runner=runner,
                                       tmp_dir=d, log=lambda *a: None)
        self.assertEqual(res["done"], 2)
        self.assertEqual(res["failed"], 1)
        self.assertFalse(res["budget_hit"])

    def test_budget_stops_between_files(self):
        """상한을 넘으면 멈춘다 — 그리고 파일 중간이 아니라 사이에서 멈춘다."""
        ticks = iter([0, 100, 200, 300, 400, 500])
        seen = []

        def runner(job):
            seen.append(job["content_id"])
            return {"ok": True, "md_path": None, "seconds": 100.0, "chars": 1}

        with tempfile.TemporaryDirectory() as d:
            res = transcribe.run_batch(self._jobs(5), "2026-2", max_seconds=250,
                                       runner=runner, clock=lambda: next(ticks),
                                       tmp_dir=d, log=lambda *a: None)
        self.assertTrue(res["budget_hit"])
        self.assertEqual(seen, ["c0", "c1"], "상한 뒤에도 계속 돌았다")
        self.assertEqual(res["done"], 2)

    def test_limit_caps_count(self):
        with tempfile.TemporaryDirectory() as d:
            res = transcribe.run_batch(
                self._jobs(5), "2026-2", limit=2, tmp_dir=d,
                runner=lambda j: {"ok": True, "md_path": None,
                                  "seconds": 1.0, "chars": 1},
                log=lambda *a: None)
        self.assertEqual(res["done"], 2)

    def test_default_max_seconds_is_three_hours(self):
        self.assertEqual(transcribe.MAX_SECONDS, 10800)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `has no attribute 'run_batch'`

- [ ] **Step 3: 구현한다**

`MODEL = ...` 줄 근처(모듈 상수 절)에 추가:

```python
# 새벽 창. 파일 **사이**에서만 검사한다 — 재개 단위가 파일이라
# 전사 중간에 끊으면 그 파일은 통째로 다시 해야 한다 (스펙 §6).
MAX_SECONDS = int(os.environ.get("TRANSCRIBE_MAX_SECONDS", "10800"))
```

`run_one` **다음**에 추가:

```python
def run_batch(jobs, semester, limit=0, max_seconds=None, tmp_dir=None,
              runner=None, clock=None, log=print):
    """영상 여러 개를 예산 안에서 돈다.

    `summarize.run` 과 같은 모양이다 — 상한에 걸리면 `budget_hit` 을 세우고
    `break`. 다음 새벽이 장부를 보고 이어받는다.
    """
    max_seconds = MAX_SECONDS if max_seconds is None else max_seconds
    # 🔴 테스트가 실제 state/tmp 를 건드리면 안 된다 — 주입 가능하게 둔다
    tmp_dir = Path(tmp_dir or (STATE_DIR / "tmp"))
    clock = clock or time.time
    runner = runner or (lambda j: run_one(j, semester, log=log))

    res = {"done": 0, "failed": 0, "skipped": 0,
           "budget_hit": False, "seconds": 0.0}
    t0 = clock()
    for job in (jobs[:limit] if limit else jobs):
        spent = clock() - t0
        if spent >= max_seconds:
            res["budget_hit"] = True
            log("  ⏱ 시간 상한 %d초 도달 — 남은 건 다음 실행이 이어받는다"
                % max_seconds)
            break
        r = runner(job)
        res["done" if r.get("ok") else "failed"] += 1
    res["seconds"] = clock() - t0
    return res
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK (**203건**)

- [ ] **Step 5: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py
git commit -m "[P2-T2] 동영상 전사 — 시간 예산 (파일 사이에서만 끊는다)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 디스크 — 실패한 mp4 를 남기되 무한정은 아니다

성공하면 mp4 를 지운다(Phase 1). **실패하면 남긴다** — 476MB 를 다시 받는 게 더 비싸다. 그런데 남기기만 하면 실패가 쌓일 때 디스크를 먹는다. 총량 상한을 두고 **오래된 것부터** 지운다. 지워져도 다음 실행이 다시 받을 뿐 손실은 없다.

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `tests/test_transcribe.py`

**Interfaces:**
- Produces: `TMP_MAX_BYTES` — `TRANSCRIBE_TMP_MAX_BYTES` 환경변수, 기본 `2147483648`
- Produces: `sweep_tmp(tmp_dir, max_bytes=None, log=None) -> int` — 지운 바이트 수
- Changes: `run_batch` 가 시작할 때 `sweep_tmp` 를 한 번 부른다

🔴 **`.part` 파일도 센다.** `download` 가 `.part` 로 받다가 죽으면 그게 남는다 — 그것까지 세지 않으면 상한이 새는 구멍이 된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class TestSweepTmp(unittest.TestCase):
    def _mk(self, d, name, size, mtime):
        p = Path(d) / name
        p.write_bytes(b"x" * size)
        os.utime(p, (mtime, mtime))
        return p

    def test_under_limit_keeps_everything(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "a.mp4", 100, 1000)
            freed = transcribe.sweep_tmp(d, max_bytes=1000)
            self.assertEqual(freed, 0)
            self.assertTrue((Path(d) / "a.mp4").exists())

    def test_deletes_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "old.mp4", 100, 1000)
            self._mk(d, "new.mp4", 100, 2000)
            freed = transcribe.sweep_tmp(d, max_bytes=150)
            self.assertEqual(freed, 100)
            self.assertFalse((Path(d) / "old.mp4").exists())
            self.assertTrue((Path(d) / "new.mp4").exists())

    def test_counts_part_files(self):
        """반쯤 받다 죽은 .part 도 디스크를 먹는다 — 안 세면 상한이 샌다."""
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "a.mp4.part", 200, 1000)
            freed = transcribe.sweep_tmp(d, max_bytes=100)
            self.assertEqual(freed, 200)
            self.assertFalse((Path(d) / "a.mp4.part").exists())

    def test_missing_dir_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(transcribe.sweep_tmp(Path(d) / "없음"), 0)

    def test_default_limit_is_two_gigabytes(self):
        self.assertEqual(transcribe.TMP_MAX_BYTES, 2147483648)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `has no attribute 'sweep_tmp'`

- [ ] **Step 3: 구현한다**

`MAX_SECONDS` 옆에 추가:

```python
# 실패한 mp4 는 남긴다(다시 받는 게 더 비싸다). 대신 총량 상한을 둔다.
TMP_MAX_BYTES = int(os.environ.get("TRANSCRIBE_TMP_MAX_BYTES", "2147483648"))
```

`run_batch` **앞**에 추가:

```python
def sweep_tmp(tmp_dir, max_bytes=None, log=None):
    """`state/tmp` 총량이 상한을 넘으면 **오래된 것부터** 지운다.

    지워져도 손실이 아니다 — 다음 실행이 다시 받을 뿐이다.
    `.part`(받다 죽은 것)도 같이 센다. 안 세면 상한이 새는 구멍이 된다.
    """
    max_bytes = TMP_MAX_BYTES if max_bytes is None else max_bytes
    d = Path(tmp_dir)
    if not d.is_dir():
        return 0
    files = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(f[1] for f in files)
    freed = 0
    for _mtime, size, p in sorted(files):        # 오래된 것부터
        if total - freed <= max_bytes:
            break
        try:
            p.unlink()
        except OSError:
            continue
        freed += size
        if log:
            log("  🗑 %s (%.0fMB) — tmp 상한 초과" % (p.name, size / 1048576.0))
    return freed
```

`run_batch` 의 `t0 = clock()` **바로 앞**에 추가 (`tmp_dir` 은 T2 에서 이미 인자로 받는다):

```python
    sweep_tmp(tmp_dir, log=log)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK (**208건**)

- [ ] **Step 5: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py
git commit -m "[P2-T3] 동영상 전사 — state/tmp 총량 상한 (오래된 것부터, .part 도 센다)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 잠금 — 크론과 수동이 겹쳐도 안전하게

03:00 크론이 도는 중에 은지가 수동으로 부르면 **같은 파일을 둘이 건드린다.** 잠금 획득 실패는 **에러가 아니다** — 크론은 조용히 빠지고 다음 주기에 다시 온다 (스펙 §8.4).

**Files:**
- Create: `src/ssu_agent/lock.py`
- Create: `tests/test_lock.py`
- Modify: `src/ssu_agent/cli.py` (`cmd_transcribe`)

**Interfaces:**
- Produces: `lock.held(path)` — 컨텍스트 매니저. 획득하면 `True`, 이미 잡혀 있으면 `False` 를 낸다(예외 아님)

```python
with lock.held(p) as ok:
    if not ok:
        ...   # 이미 돌고 있다
```

🔴 **커널이 자동 해제한다.** 프로세스가 죽어도 잠금이 남지 않는다 — 그래서 "낡은 락 파일 지우기" 같은 코드를 **쓰지 않는다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_lock.py` 를 **새로 만든다**:

```python
# -*- coding: utf-8 -*-
"""전사 잠금 — 크론과 수동이 겹치는 걸 막는다 (스펙 §8.4)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ssu_agent import lock


class TestHeld(unittest.TestCase):
    def test_acquires_when_free(self):
        with tempfile.TemporaryDirectory() as d:
            with lock.held(Path(d) / "t.lock") as ok:
                self.assertTrue(ok)

    def test_second_holder_gets_false_not_exception(self):
        """획득 실패는 에러가 아니다 — 크론이 조용히 빠져야 한다."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.lock"
            with lock.held(p) as first:
                self.assertTrue(first)
                with lock.held(p) as second:
                    self.assertFalse(second)

    def test_released_after_block(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.lock"
            with lock.held(p) as ok:
                self.assertTrue(ok)
            with lock.held(p) as again:
                self.assertTrue(again, "블록을 나왔는데 잠금이 안 풀렸다")

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "없던폴더" / "t.lock"
            with lock.held(p) as ok:
                self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
```

⚠️ **같은 프로세스 안에서 `flock` 이 두 번 잡히지 않으려면 파일 디스크립터가 달라야 한다.** 위 테스트는 `held` 가 매번 새로 `open` 하므로 성립한다 — 구현이 fd 를 캐시하면 `test_second_holder_gets_false_not_exception` 이 깨진다. 그게 이 테스트의 일이다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_lock -q`
Expected: FAIL — `No module named 'ssu_agent.lock'`

- [ ] **Step 3: 구현한다**

`src/ssu_agent/lock.py` 를 새로 만든다:

```python
# -*- coding: utf-8 -*-
"""파일 잠금 — 크론과 수동이 같은 일을 동시에 하는 걸 막는다.

🔴 커널이 자동 해제하므로 **프로세스가 죽어도 잠금이 남지 않는다.**
"낡은 락 파일 지우기" 같은 코드를 쓰지 마라 — 그게 오히려 남의 잠금을 깬다.
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def held(path):
    """잡으면 True, 이미 잡혀 있으면 False 를 낸다. **예외가 아니다.**"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(str(p), "a+")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        f.close()          # close 가 잠금을 푼다
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_lock -q`
Expected: OK (4건)

- [ ] **Step 5: CLI 를 배선한다**

`src/ssu_agent/cli.py` 의 `cmd_transcribe` 를 **통째로** 아래로 바꾼다:

```python
def cmd_transcribe(a):
    """전사. 예산·장부·잠금이 붙어 있어 크론에 걸어도 된다."""
    from . import lock
    from . import transcribe as tr
    from .config import STATE_DIR      # cli.py 상단은 `ROOT, get` 만 가져온다
    cfg = get()
    snap = _snapshot(refresh=a.refresh)
    jobs = tr.plan(snap)
    print("전사 대상 %d개" % len(jobs))
    if a.dry_run:
        for j in jobs[:a.limit or len(jobs)]:
            print("  %-22s W%02d %-30s %.0f분"
                  % (j["stem"], j["week"], (j["title"] or "")[:30],
                     (j["duration"] or 0) / 60))
        return 0

    with lock.held(STATE_DIR / "transcribe.lock") as ok:
        if not ok:
            # 크론이 도는 중이다. 에러가 아니다 — 조용히 빠진다 (스펙 §8.4)
            print("이미 돌고 있다. 진행상황은 나중에 확인해.")
            return 0
        res = tr.run_batch(jobs, cfg.semester, limit=a.limit,
                           max_seconds=a.max_seconds)
    _sum("전사 {done}건 · 실패 {failed}건 · {mins:.0f}분 걸림{hit}".format(
        mins=res["seconds"] / 60.0,
        hit=" · ⏱ 시간 상한" if res["budget_hit"] else "", **res))
    return 0
```

파서에 한 줄 추가 (`tc.add_argument("--model", ...)` 다음):

```python
    tc.add_argument("--max-seconds", type=int, default=None,
                    dest="max_seconds",
                    help="실행당 시간 상한 (기본 TRANSCRIBE_MAX_SECONDS=10800)")
```

⚠️ **`--model` 은 이제 `run_batch` 로 안 넘어간다.** `run_batch` 의 기본 `runner` 는 `run_one(j, semester, log=log)` 라 모델을 받지 않는다. 모델을 바꾸려면 `TRANSCRIBE_MODEL` 환경변수를 쓴다 — `--model` 도움말을 그렇게 고친다:

```python
    tc.add_argument("--model", default=None,
                    help="(미사용 — TRANSCRIBE_MODEL 환경변수를 써라)")
```

- [ ] **Step 6: 실제로 돌려 확인한다**

```bash
./bin/ssu-agent transcribe --dry-run --limit 3     # 목록이 나온다
./bin/ssu-agent transcribe --limit 1 --max-seconds 1
```

Expected: 두 번째는 **시간 상한 1초**라 아무것도 안 돌고 `⏱ 시간 상한` 이 뜬다
(잠금·장부·예산 배선이 살아 있는지 네트워크 없이 확인하는 방법이다).

- [ ] **Step 7: 커밋**

```bash
git add src/ssu_agent/lock.py tests/test_lock.py src/ssu_agent/cli.py
git commit -m "[P2-T4] 동영상 전사 — flock 잠금 + CLI 배선 (크론·수동 충돌 방지)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 완료 조건

- [ ] 테스트 **212건** OK (194 + 5 + 4 + 5 + 4)
- [ ] `./bin/ssu-agent transcribe --limit 1 --max-seconds 1` 이 `⏱ 시간 상한` 으로 빠진다
- [ ] `docs/superpowers/specs/...-design.md` §5·§6·§8.4 에 미구현으로 남은 항목이 없다
- [ ] vault `HISTORY.md` 한 줄 + `05_AI/작업중/2026-09-05_동영상전사_설계.md` 갱신

## 이 계획 다음

- **Phase 3** — 요약 연결: `summarize._targets` 가 전사본 `.md` 도 보게(`summarize.py:275`), `_markdown_path` 확장자 처리, `materials.IGNORED_TYPES` 에서 `movie`·`everlec` 제거. **전문용어·인명 보정 프롬프트도 여기서** (교수명 "이경아"→"이경화" 가 남아 있다)
- **Phase 4** — `status` / `pending` / `transcribe --background` (스펙 §8.2·§8.3)
- **Phase 5** — 03:00 크론 배선 (`com.eunzi.ssu-materials` 옆)

## 안 하는 것 (이 Phase 에서)

- **세그먼트 단위 재개** — 재개 단위는 파일이다. 3시간 창에 18~19개가 들어가고(6.0시간 백로그 ÷ 3.06×) 잘리는 건 항상 마지막 1개다
- **낡은 락 파일 청소** — `flock` 은 커널이 푼다. 청소 코드가 오히려 남의 잠금을 깬다
- **`state/tmp` 를 크론과 별개로 도는 청소 잡** — `run_batch` 시작 때 한 번이면 충분하다
