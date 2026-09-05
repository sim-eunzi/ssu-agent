# 동영상 전사 Phase 1 — 영상 1개 end-to-end 실측

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동영상 강의 **1개**를 받아 전사해 `markdown/{제목}.md` 로 떨구는 수직 슬라이스를 만들고, **실제 속도와 한국어 품질을 측정한다.**

**Architecture:** 새 모듈 `transcribe.py` 하나. 순수 함수(파싱·계획·렌더) → 취득(Range 스트리밍) → 전사(faster-whisper) → CLI 순으로 쌓는다. 무거운 의존성은 **함수 안에서만 import** 한다.

**Tech Stack:** Python 3.8+ stdlib · `faster-whisper`(함수 내 import) · unittest

**Spec:** `docs/superpowers/specs/2026-09-05-video-transcription-design.md`

## Global Constraints

- **의존성 0 규약** — `faster_whisper` 는 **함수 안에서만** import 한다. `sync`·`vault-sync`·`materials`·`brief` 는 import 만으로 실패하면 안 된다 (`summarize.extract_pdf` 와 같은 규약).
- **테스트는 네트워크·모델을 타지 않는다.** 픽스처는 `docs/samples/`. 기존 166건이 모두 그렇다.
- **테스트 실행** — `python3 -m unittest discover -s tests -t .` (pytest 없음)
- **전사본 위치** — `data/{학기}/{과목}/W{주차}/markdown/{안전제목}.md`
- **`meta.json` 계약** — `items[{content_id}].file` 은 `"{안전제목}.md"`. `summarize._targets` 가 이 값을 본다. 확장자를 바꾸면 요약이 조용히 안 돈다.
- **모델 기본값** — `TRANSCRIBE_MODEL`, 미설정 시 `small`. `compute_type="int8"`, `device="cpu"` (이 iMac 은 Intel·GPU 없음).
- **작업 파일** — `state/tmp/{content_id}.mp4`. `state/` 는 git 미추적.
- **커밋 규약** — 태스크마다 1커밋. 메시지 첫 줄에 `[T{번호}]` 를 넣어 `git log --oneline` 으로 재개 지점을 찾는다.

## ✅ Phase 1 완료 (2026-09-05) — 다음은 **모델 결정 → Phase 2**

수직 슬라이스가 끝까지 돈다. 실측 숫자는 맨 아래 「실측 결과」에 있다.
**Phase 2 를 쓰기 전에 `small`/`medium` 을 정해야 한다** — 예산이 배속에 걸려 있다.

## 진행 상황 (세션이 끊기면 여기부터)

- [x] **T1 순수 함수** — `parse_media_uri` · `plan` · `to_markdown` ✅ 2026-09-05 (13건, 전체 179건 OK)
- [x] **T2 취득** — `download` (Range 이어받기, 스트리밍) ✅ 2026-09-05 (5건, 전체 184건 OK)
- [x] **T3 전사** — `transcribe_file` + `faster-whisper` 설치 ✅ 2026-09-05 (2건, 전체 186건 OK)
- [x] **T4 CLI + 실측** — `ssu-agent transcribe` ✅ 2026-09-05 (2건, 전체 188건 OK)

`git log --oneline | grep '\[T'` 로 어디까지 했는지 확인한다.

---

### Task 1: 순수 함수 (파싱·계획·렌더)

네트워크도 모델도 안 타는 부분부터. 여기가 서면 나머지는 배선이다.

**Files:**
- Create: `src/ssu_agent/transcribe.py`
- Create: `tests/test_transcribe.py`
- Create: `docs/samples/commons_content_video.xml`

**Interfaces:**
- Consumes: `materials.content_id_of(item)` → `str|None` (기존)
- Produces:
  - `parse_media_uri(xml: str) -> str|None`
  - `plan(snap: dict) -> list[dict]` — 각 항목 `{stem, week, content_id, item_id, title, duration, source}`
  - `safe_stem(title: str, content_id: str) -> str` — 파일명 안전화(확장자 없음)
  - `to_markdown(segments: list, meta: dict) -> str`

- [ ] **Step 1: 픽스처를 만든다**

정찰 때 받은 실제 응답이다. `<author><name>` 은 저장소 규약대로 마스킹한다.

```bash
cat > docs/samples/commons_content_video.xml <<'XML'
<?xml version="1.0"?>
<content version="1.0"><content_playing_info version="1.0"><content_id>68b27aeb26a32</content_id><content_type>video1</content_type><content_uri><![CDATA[https://commons.ssu.ac.kr/contents31/ssu1000001/68b27aeb26a32/contents/]]></content_uri><content_duration>3463.98</content_duration><content_thumbnail_uri>https://commons.ssu.ac.kr/contents31/ssu1000001/68b27aeb26a32/contents/web_files/slides/thumbnails/big_thumbnail.png</content_thumbnail_uri><main_media><desktop><html5><method>progressive</method><media_uri>https://ssuin-object.commonscdn.com/ssu-contents/contents31/ssu1000001/68b27aeb26a32/contents/media_files/mobile/ssmovie.mp4</media_uri></html5><flash_fallback><method>pseudo</method><media_uri>https://commons.ssu.ac.kr/contents31_pseudo/ssu1000001/68b27aeb26a32/contents/media_files/mobile/ssmovie.mp4</media_uri></flash_fallback></desktop><mobile><html5><method>progressive</method><media_uri>https://ssuin-object.commonscdn.com/ssu-contents/contents31/ssu1000001/68b27aeb26a32/contents/media_files/mobile/ssmovie.mp4</media_uri></html5></mobile></main_media></content_playing_info><content_metadata version="1.0"><title><![CDATA[1-2강의]]></title><summary><![CDATA[]]></summary><tags><![CDATA[]]></tags><author><name><![CDATA[홍길동]]></name><email><![CDATA[]]></email></author><date><![CDATA[2025-08-30 13:15:39]]></date></content_metadata></content>
XML
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

```python
# -*- coding: utf-8 -*-
"""동영상 전사 — 순수 함수."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ssu_agent import transcribe

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "docs", "samples")


def _xml():
    with open(os.path.join(SAMPLES, "commons_content_video.xml"), encoding="utf-8") as f:
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
        return {"courses": {"47737": {"stem": "정치철학-한나아렌트", "items": [item]}}}

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
```

- [ ] **Step 3: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ssu_agent.transcribe'`

- [ ] **Step 4: 최소 구현을 쓴다**

```python
# -*- coding: utf-8 -*-
"""동영상 강의 전사 (Phase 1).

    movie item ──content.php──> <media_uri> ──> mp4 ──whisper──> markdown/{제목}.md
                                                                        │
                                              기존 summarize 경로 ──────┘

🔴 **자막 트랙이 없다.** 2026-09-05 정찰에서 caption/vtt/smi 도, 슬라이드
인덱스도 전부 없었다. 그래서 전사가 필수다.

의존성은 `transcribe_file` 안에서만 import 한다 — `summarize.extract_pdf` 와
같은 규약이라 나머지 모듈의 '의존성 0' 이 그대로다.
"""

import re

from . import materials

# main_media 안의 첫 media_uri 가 desktop>html5>progressive(CDN) 다.
# flash_fallback(pseudo)은 그 뒤에 온다 — 순서에 의존하지 않게 html5 블록만 본다.
HTML5_RE = re.compile(r"<html5>.*?</html5>", re.S)
MEDIA_URI_RE = re.compile(
    r"<media_uri>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</media_uri>", re.S)

VIDEO_TYPES = ("movie", "everlec")
UNSAFE_RE = re.compile(r"[^\w가-힣\-. ]+")


def parse_media_uri(xml):
    """`content.php` 응답에서 받을 수 있는 mp4 URL. 없으면 None."""
    if not xml:
        return None
    for block in HTML5_RE.findall(xml):
        m = MEDIA_URI_RE.search(block)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def plan(snap):
    """스냅샷 → 전사 대상. 주차를 모르면 어디에 둘지 모르므로 뺀다."""
    out = []
    for _cid, e in sorted((snap.get("courses") or {}).items()):
        for it in e.get("items") or []:
            if it.get("kind") != "lecture" or it.get("unopened"):
                continue
            if (it.get("content_type") or "").lower() not in VIDEO_TYPES:
                continue
            cid, wk = materials.content_id_of(it), it.get("week")
            if not cid or wk is None:
                continue
            out.append({"stem": e.get("stem"), "week": int(wk),
                        "content_id": cid, "item_id": it.get("item_id"),
                        "title": it.get("title") or "",
                        "duration": it.get("duration"),
                        "source": it.get("view_url")})
    return out


def safe_stem(title, content_id):
    """파일명으로 쓸 수 있는 제목(확장자 없음). 비면 content_id 로."""
    s = UNSAFE_RE.sub(" ", title or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s or content_id


def _mmss(sec):
    sec = int(sec or 0)
    return "%02d:%02d" % (sec // 60, sec % 60)


def to_markdown(segments, meta):
    """전사본. 머리말에 출처·길이·모델을 남긴다 —
    나중에 모델을 올렸을 때 **무엇을 다시 돌려야 하는지** 알아야 한다."""
    mins = int((meta.get("duration") or 0) // 60)
    head = "> 출처 %s · %d분 · whisper %s · %s\n" % (
        meta.get("source") or "", mins, meta.get("model") or "", meta.get("at") or "")
    body = "\n".join("[%s] %s" % (_mmss(s.get("start")), (s.get("text") or "").strip())
                     for s in segments or [])
    return head + "\n" + body + "\n"
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: PASS (14건)

- [ ] **Step 6: 전체 스위트가 안 깨졌는지 본다**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK (기존 166 + 신규)

- [ ] **Step 7: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py docs/samples/commons_content_video.xml
git commit -m "[T1] 동영상 전사 — 순수 함수 (media_uri 파싱·대상 선정·전사본 렌더)"
```

---

### Task 2: 취득 — Range 로 이어받는 스트리밍 다운로드

🔴 **`net.request` 를 쓰면 안 된다.** 응답 전체를 메모리에 읽는다(`_body`) — 476MB 짜리에 쓰면 안 된다. 여기만 `urllib` 을 직접 쓴다.

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `tests/test_transcribe.py`

**Interfaces:**
- Produces: `download(url, dest, open_url=None, chunk=1<<20, log=None) -> int` (받은 총 바이트)
  - `open_url(url, headers) -> 응답객체` 를 주입받는다. 응답객체는 `.status`, `.headers`, `.read(n)` 을 가진다. 테스트는 가짜를 넣는다.
  - `.part` 로 받고 완결되면 `dest` 로 rename — 반쯤 받은 파일을 전사하지 않기 위해서다.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_transcribe.py` 에 추가)

```python
import io
import tempfile
from pathlib import Path


class _FakeResp:
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
            part = Path(str(dest) + ".part")
            part.write_bytes(b"abc")
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `AttributeError: module 'ssu_agent.transcribe' has no attribute 'download'`

- [ ] **Step 3: 구현한다** (`transcribe.py` 에 추가)

```python
import os
import urllib.request

from .net import UA


def _open_url(url, headers):
    req = urllib.request.Request(url, headers=dict(headers or {}))
    req.add_header("User-Agent", UA)
    return urllib.request.urlopen(req, timeout=60)


def download(url, dest, open_url=None, chunk=1 << 20, log=None):
    """`Range` 로 이어받는 스트리밍 다운로드. 받은 총 바이트를 낸다.

    🔴 `net.request` 를 쓰지 않는다 — 응답 전체를 메모리에 읽기 때문이다.
    476MB 짜리를 그렇게 받으면 안 된다.

    `.part` 로 받고 완결되면 rename 한다. 반쯤 받은 파일이 `dest` 에
    보이면 전사가 그걸 집어간다.
    """
    open_url = open_url or _open_url
    dest = str(dest)
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": "bytes=%d-" % have} if have else {}

    with open_url(url, headers) as r:
        # 206 이 아니면 서버가 Range 를 무시한 것이다. 이어붙이면 깨진다.
        if have and getattr(r, "status", 200) != 206:
            have = 0
            headers = {}
        mode = "ab" if have else "wb"
        with open(part, mode) as f:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                f.write(buf)
                have += len(buf)
    os.replace(part, dest)
    if log:
        log("  ↓ %s (%.0fMB)" % (os.path.basename(dest), have / 1048576.0))
    return have
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: PASS

- [ ] **Step 5: 전체 스위트**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK

- [ ] **Step 6: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py
git commit -m "[T2] 동영상 전사 — Range 이어받기 스트리밍 다운로드"
```

---

### Task 3: 전사 — `faster-whisper` 배선

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `tests/test_transcribe.py`
- Modify: `src/ssu_agent/cli.py` (doctor 에 한 줄)

**Interfaces:**
- Produces: `transcribe_file(path, model=None, language="ko") -> list[dict]`
  - 각 원소 `{"start": float, "end": float, "text": str}` — `to_markdown` 이 먹는 형식과 같다.

- [ ] **Step 1: 의존성을 설치한다**

```bash
pip3 install faster-whisper
python3 -c "import faster_whisper; print(faster_whisper.__version__)"
```

Expected: 버전이 찍힌다. 실패하면 여기서 멈추고 보고한다 (Intel macOS 용 `ctranslate2` 휠이 없으면 설계를 다시 봐야 한다).

- [ ] **Step 2: 실패하는 테스트를 쓴다**

모델은 안 부른다 — **없어도 도는 것**만 검증한다.

```python
class TestTranscribeFile(unittest.TestCase):
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
```

- [ ] **Step 3: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `has no attribute 'transcribe_file'`

- [ ] **Step 4: 구현한다**

```python
MODEL = os.environ.get("TRANSCRIBE_MODEL", "small")
_MODELS = {}


def _load_model(name):
    """모델은 무겁다(수백 MB). 한 프로세스 안에서 재사용한다."""
    if name not in _MODELS:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper 가 없다: pip3 install faster-whisper") from e
        # 이 iMac 은 Intel·GPU 없음 — cpu/int8 이 유일하게 현실적인 조합이다.
        _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODELS[name]


def transcribe_file(path, model=None, language="ko"):
    """mp4 → 세그먼트 목록. `to_markdown` 이 먹는 형식 그대로 낸다."""
    m = _load_model(model or MODEL)
    segments, _info = m.transcribe(str(path), language=language, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: PASS

- [ ] **Step 6: `doctor` 에 한 줄 추가**

`src/ssu_agent/cli.py` 의 `cmd_doctor` 는 `w = "{:<17} {}"` 로 줄을 맞춘다.
`outbox` 줄 다음, `알림` 줄 앞에 같은 형식으로 넣는다.
(⚠️ `cmd_doctor` 에는 `pymupdf4llm` 보고 줄이 **없다** — 새로 만드는 것이다.)

```python
    try:
        import faster_whisper                     # noqa: F401
        print(w.format("faster-whisper", "✅"))
    except ImportError:
        print(w.format("faster-whisper",
                       "⚠️ 없음 — transcribe 만 못 쓴다 (pip3 install faster-whisper)"))
```

- [ ] **Step 7: 확인**

Run: `./bin/ssu-agent doctor`
Expected: `faster-whisper  ✅` 가 보인다

- [ ] **Step 8: 커밋**

```bash
git add src/ssu_agent/transcribe.py tests/test_transcribe.py src/ssu_agent/cli.py
git commit -m "[T3] 동영상 전사 — faster-whisper 배선 (함수 내 import·cpu/int8)"
```

---

### Task 4: CLI + **실측**

여기가 Phase 1 의 목적이다 — **실제 데이터가 잘 나오는지 본다.**

**Files:**
- Modify: `src/ssu_agent/transcribe.py`
- Modify: `src/ssu_agent/cli.py`
- Modify: `tests/test_transcribe.py`

**Interfaces:**
- Produces: `run_one(job, semester, root=None, fetch=None, transcriber=None, log=print) -> dict`
  - `fetch(url, dest)` 와 `transcriber(path)` 를 주입받는다 — 테스트가 네트워크·모델 없이 전 구간을 돈다.
  - 반환 `{"ok": bool, "md_path": str, "seconds": float, "chars": int}`

- [ ] **Step 1: 실패하는 통합 테스트를 쓴다**

```python
import json


class TestRunOne(unittest.TestCase):
    def test_end_to_end_with_fakes(self):
        job = {"stem": "정치철학-한나아렌트", "week": 1,
               "content_id": "68b27aeb26a32", "title": "1-2 한나 아렌트",
               "duration": 3463.98,
               "source": "https://commons.ssu.ac.kr/em/68b27aeb26a32"}

        def fetch(url, dest):
            open(dest, "wb").write(b"fake mp4")
            return 8

        def transcriber(path):
            return [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            res = transcribe.run_one(job, "2026-2", root=root,
                                     fetch=fetch, transcriber=transcriber,
                                     resolve=lambda cid: "http://x/v.mp4",
                                     log=lambda *a: None)
            self.assertTrue(res["ok"])
            md = root / "2026-2" / "정치철학-한나아렌트" / "W01" / "markdown" / "1-2 한나 아렌트.md"
            self.assertTrue(md.exists())
            self.assertIn("[00:00] 안녕하세요", md.read_text(encoding="utf-8"))

            # meta.json 계약 — summarize._targets 가 이 값을 본다
            meta = json.loads((root / "2026-2" / "정치철학-한나아렌트" / "W01"
                               / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["items"]["68b27aeb26a32"]["file"],
                             "1-2 한나 아렌트.md")

    def test_mp4_is_deleted_on_success(self):
        job = {"stem": "선형대수", "week": 1, "content_id": "cid1",
               "title": "선대 1-1", "duration": 60, "source": "s"}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tmp = root / "tmp"
            transcribe.run_one(
                job, "2026-2", root=root, tmp_dir=tmp,
                resolve=lambda cid: "http://x/v.mp4",
                fetch=lambda u, p: (open(p, "wb").write(b"x"), 1)[1],
                transcriber=lambda p: [{"start": 0, "end": 1, "text": "가"}],
                log=lambda *a: None)
            self.assertEqual(list(tmp.glob("*.mp4")), [])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_transcribe -q`
Expected: FAIL — `has no attribute 'run_one'`

- [ ] **Step 3: 구현한다**

```python
import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, KST, STATE_DIR


def resolve_media(content_id):
    """content.php → 받을 수 있는 mp4 URL."""
    from . import net
    xml = net.get_text(materials.CONTENT_PHP % content_id)
    return parse_media_uri(xml)


def week_dir(semester, stem, week, root=None):
    return Path(root or DATA_DIR) / semester / stem / ("W%02d" % int(week))


def _update_meta(wd, job, fname):
    """`summarize._targets` 가 읽는 계약. 확장자를 바꾸면 요약이 조용히 안 돈다."""
    p = wd / "meta.json"
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    meta.setdefault("course", job["stem"])
    meta.setdefault("week", int(job["week"]))
    meta.setdefault("items", {})
    meta["items"][job["content_id"]] = {
        "file": fname, "title": job.get("title"),
        "source": job.get("source"), "kind": "video",
        "duration": job.get("duration"),
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8")


def run_one(job, semester, root=None, tmp_dir=None, resolve=None,
            fetch=None, transcriber=None, model=None, log=print):
    """영상 하나를 받아 전사해 `markdown/{제목}.md` 로 떨군다.

    네트워크·모델을 주입받는다 — 테스트가 전 구간을 돌 수 있어야 한다.
    """
    import time as _t
    resolve = resolve or resolve_media
    fetch = fetch or (lambda url, dest: download(url, dest, log=log))
    transcriber = transcriber or (lambda p: transcribe_file(p, model=model))

    wd = week_dir(semester, job["stem"], job["week"], root)
    stem = safe_stem(job.get("title"), job["content_id"])
    md_path = wd / "markdown" / (stem + ".md")
    if md_path.exists():
        log("  ⤼ %s — 이미 전사됨" % stem)
        return {"ok": True, "md_path": str(md_path), "seconds": 0.0,
                "chars": len(md_path.read_text(encoding="utf-8"))}

    url = resolve(job["content_id"])
    if not url:
        log("  ✖ %s — media_uri 를 못 찾았다" % stem)
        return {"ok": False, "md_path": None, "seconds": 0.0, "chars": 0}

    tmp = Path(tmp_dir or (STATE_DIR / "tmp"))
    tmp.mkdir(parents=True, exist_ok=True)
    mp4 = tmp / (job["content_id"] + ".mp4")

    t0 = _t.time()
    fetch(url, str(mp4))
    segs = transcriber(str(mp4))
    took = _t.time() - t0

    md = to_markdown(segs, {"source": job.get("source"),
                            "duration": job.get("duration"),
                            "model": model or MODEL,
                            "at": datetime.now(KST).strftime("%Y-%m-%d")})
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    _update_meta(wd, job, md_path.name)

    try:                       # 성공했으면 영상은 필요 없다. 원하는 건 텍스트다.
        mp4.unlink()
    except OSError:
        pass

    dur = float(job.get("duration") or 0)
    log("  ✓ %s — %.0f초 걸림 (%.1f× 실시간) · %d자"
        % (stem, took, (dur / took) if took else 0, len(md)))
    return {"ok": True, "md_path": str(md_path), "seconds": took, "chars": len(md)}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest discover -s tests -t . -q`
Expected: OK

- [ ] **Step 5: CLI 를 붙인다**

`src/ssu_agent/cli.py` — `cmd_materials` 옆에 같은 형식으로.

```python
def cmd_transcribe(a):
    """Phase 1 — 영상 1개 실측용. 예산·장부·잠금은 Phase 2 에서 붙는다."""
    from . import transcribe as tr
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
    for j in jobs[:a.limit or len(jobs)]:
        tr.run_one(j, cfg.semester, model=a.model)
    return 0
```

파서 등록 (`build_parser` 안, `materials` 옆):

```python
    tc = sub.add_parser("transcribe", help="동영상 강의 전사 (Phase 1: 실측용)")
    tc.add_argument("--refresh", action="store_true", help="먼저 sync 한다")
    tc.add_argument("--dry-run", action="store_true", help="대상만 센다")
    tc.add_argument("--limit", type=int, default=0, help="N개만 처리")
    tc.add_argument("--model", default=None, help="기본 TRANSCRIBE_MODEL(small)")
    tc.set_defaults(func=cmd_transcribe)
```

- [ ] **Step 6: dry-run 으로 대상을 확인한다**

Run: `./bin/ssu-agent transcribe --dry-run --limit 5`
Expected: 영상 목록이 주차·길이와 함께 나온다 (네트워크 다운로드 없음)

- [ ] **Step 7: 🔴 실측 — 영상 1개를 끝까지 돌린다**

**가장 짧은 영상을 고른다.** `--dry-run` 출력에서 길이가 가장 작은 것을 보고,
그 과목만 남기고 싶으면 `--limit 1` 이 첫 항목을 잡으므로 필요하면 순서를 확인한다.

```bash
time ./bin/ssu-agent transcribe --limit 1
```

측정할 것 (Phase 2 의 모든 예산이 이 숫자에 걸려 있다):

1. **속도** — 출력의 `N× 실시간`
2. **한국어 품질** — 전사본을 직접 열어 읽는다. 전문용어·인명이 살아남는가
3. **PyAV 디코딩** — `ffmpeg` 없이 됐는가 (에러 없이 돌면 된 것)
4. **파일** — `data/{학기}/{과목}/W{주차}/markdown/*.md` 와 `meta.json` 확인
5. **정리** — `state/tmp/` 가 비었는가

```bash
ls -la state/tmp/
find data -name '*.md' -newer README.md | head
```

- [ ] **Step 8: 실측 결과를 기록한다**

`docs/superpowers/plans/2026-09-05-video-transcription-phase1.md` 맨 아래
「실측 결과」 절에 숫자를 적는다. **Phase 2 계획이 이 숫자를 근거로 쓰인다.**

- [ ] **Step 9: 커밋**

```bash
git add -A
git commit -m "[T4] 동영상 전사 — CLI + 영상 1개 실측"
```

---

## 실측 결과 (2026-09-05)

**영상:** `창의융합인재되기-3code` W01 「Deep learning creativity - 창의성의 6P - 1주차」
· 380.04초(6.3분) · mp4 49.9MB (다운로드 6.2초)

| 항목 | `small` | `medium` |
|---|---|---|
| 전사 소요 | **124초** | **860초** |
| 배속 | **3.06× 실시간** | **0.44× 실시간** |
| 설계 추정치 | 2.5× (낙관이 아니라 **보수적**이었다) | 1× (**2.3배 낙관**이었다) |
| 전사본 글자 수 | 2,175자 | 2,167자 |
| 세그먼트 | 49개 | 95개 |
| 36시간 백로그 | **11.8시간** | **82시간** |
| 새벽 3시간 창으로 | **4일** | **27일** |

- **PyAV 디코딩** — ✅ 시스템 `ffmpeg` 없이 됐다 (`av` 15.1.0 번들). 설계 가정이 맞았다.
- **Intel macOS 휠** — ✅ 있다. `ctranslate2` 4.8.2 · `faster-whisper` 1.2.1 · Python 3.9.6
- **모델 최초 다운로드** — `small` ~0.5GB · `medium` ~1.5GB. 첫 실행에만 든다
  (end-to-end 첫 실행 296초 중 절반 이상이 이거였다 — **배속 숫자에 섞으면 안 된다**).
- **정리** — `state/tmp/` 비었고 `meta.json` 계약(`items[cid].file = "{제목}.md"`) 맞다.

### 🔴 한국어 품질 — 배속보다 이쪽이 결정적이다

같은 문장을 두 모델이 이렇게 냈다:

| 원문(추정) | `small` | `medium` |
|---|---|---|
| 딥러닝 | **빔 러닝** | 딥러닝 ✅ |
| 무려 2년 | **우려 2년** | 무려 2년 ✅ |
| big C, little c, mini c, pro c | **빅스이, 리러스이, 미니스이, 프로스이** | big C, little C, mini C, pro C ✅ |
| 브레인스토밍 | **브랜스톰 링** | (해당 구간 정상) |
| 발현 | **발연** | ✅ |
| 신경망 | 신경방 ❌ | 신경방 ❌ (둘 다 틀림) |

일반 서술문은 `small` 도 읽힌다. **깨지는 건 전문용어와 영어 혼용** —
즉 **강의에서 값어치 있는 부분만 골라서 깨진다.** 제목의 "Deep learning" 이
본문에서 "빔 러닝" 이 되는 전사본은 검색 아카이브로도, LLM 요약 입력으로도
쓰기 어렵다 (틀린 용어가 요약에 그대로 올라간다).

**판단: `medium` 승격 (권고).** 근거 —

1. **아카이브는 한 번 만들고 영구 보존한다.** 틀린 전사본은 영원히 틀리고,
   나중에 고치려면 전사 비용을 처음부터 다시 낸다. 속도는 하루가 아깝고
   품질은 학기가 아깝다.
2. **27일은 감당된다.** 은지 결정 #1 이 "아카이브"였지 "오늘 다 봐야 한다"가
   아니었다. 새벽에만 도니 낮 체감이 0 이고, **새 주차는 주당 ~3시간**이라
   백로그만 넘기면 medium 이 실시간을 3배 앞선다 (3시간 창 ÷ 0.44× = 1.3시간/일 = 9시간/주).
3. **비용은 그대로 $0.** 전사는 로컬이라 medium 이어도 돈이 안 든다.

`small` 을 고르는 경우는 "지금 당장 검색되는 게 낫다" 일 때다.
**모델은 `TRANSCRIBE_MODEL` 환경변수 하나라** 나중에 갈아끼울 수 있지만,
**이미 전사한 파일은 자동으로 다시 안 돈다**(`md_path.exists()` 로 건너뛴다) —
바꾸려면 해당 `.md` 를 지워야 한다.

⚠️ **T4 실측으로 남은 전사본 1건은 `small` 로 만들어졌다**
(`data/2026-2/창의융합인재되기-3code/W01/markdown/Deep learning creativity - 창의성의 6P - 1주차.md`).
`medium` 으로 정하면 이 파일을 지우고 다시 돌린다.

---

## 이 계획 다음 (별도 계획 문서로)

Phase 1 이 끝나고 **실측 숫자가 나온 뒤에** 각각 계획을 쓴다. 지금 쓰면
추정치 위에 설계하게 된다.

- **Phase 2** — 예산(시간 상한)·장부(`.progress`)·잠금(`flock`)·`state/tmp` 총량 상한
- **Phase 3** — `summarize.py` 3곳 + `materials.py` 제외 해제 (요약까지 연결)
- **Phase 4** — `status` / `pending` / `transcribe --background` (스펙 §8)
- **Phase 5** — 03:00 크론 배선
