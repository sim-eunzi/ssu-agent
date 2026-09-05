# -*- coding: utf-8 -*-
"""동영상 강의 전사 (Phase 1).

    movie item ──content.php──> <media_uri> ──> mp4 ──whisper──> markdown/{제목}.md
                                                                        │
                                              기존 summarize 경로 ──────┘

🔴 **자막 트랙이 없다.** 2026-09-05 정찰에서 caption/vtt/smi 도, 슬라이드
인덱스(`web_files/*.xml`)도 전부 없었다. 그래서 전사가 필수다.

무거운 의존성은 **함수 안에서만** import 한다 — `summarize.extract_pdf` 와
같은 규약이라 `sync`·`vault-sync`·`materials`·`brief` 의 '의존성 0' 이 그대로다.

스펙: docs/superpowers/specs/2026-09-05-video-transcription-design.md
"""

import os
import re
import urllib.request

from . import materials
from .net import UA

# 🔴 `main_media` 에는 media_uri 가 여럿이다 — desktop>html5(progressive, CDN),
#    desktop>flash_fallback(pseudo), mobile>html5. **순서에 의존하지 않으려고**
#    html5 블록만 보고 고른다. flash_fallback 은 pseudo 스트리밍이라 못 받는다.
HTML5_RE = re.compile(r"<html5>.*?</html5>", re.S)
MEDIA_URI_RE = re.compile(
    r"<media_uri>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</media_uri>", re.S)

VIDEO_TYPES = ("movie", "everlec")
UNSAFE_RE = re.compile(r"[^\w\-. ]+", re.U)


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
    """스냅샷 → 전사 대상.

    주차를 모르면 어디에 둘지 모르므로 뺀다. 미개봉(404)은 `view_url` 자체가
    없어서 어차피 못 받는다 (핸드오프 §2.2).
    """
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
    """파일명으로 쓸 수 있는 제목(확장자 없음). 비면 content_id 로.

    `\\w` 가 유니코드 기본이라 한글은 그대로 살아남는다.
    """
    s = UNSAFE_RE.sub(" ", title or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s or content_id


def _mmss(sec):
    sec = int(sec or 0)
    return "%02d:%02d" % (sec // 60, sec % 60)


def to_markdown(segments, meta):
    """전사본.

    머리말에 출처·길이·모델을 남긴다 — 나중에 모델을 올렸을 때
    **무엇을 다시 돌려야 하는지** 알 수 있어야 한다.
    """
    mins = int((meta.get("duration") or 0) // 60)
    head = "> 출처 %s · %d분 · whisper %s · %s\n" % (
        meta.get("source") or "", mins,
        meta.get("model") or "", meta.get("at") or "")
    body = "\n".join(
        "[%s] %s" % (_mmss(s.get("start")), (s.get("text") or "").strip())
        for s in segments or [])
    return head + "\n" + body + "\n"


# ------------------------------------------------------------------ 취득
def _open_url(url, headers):
    req = urllib.request.Request(url, headers=dict(headers or {}))
    req.add_header("User-Agent", UA)
    return urllib.request.urlopen(req, timeout=60)


def download(url, dest, open_url=None, chunk=1 << 20, log=None):
    """`Range` 로 이어받는 스트리밍 다운로드. 받은 총 바이트를 낸다.

    🔴 **`net.request` 를 쓰지 않는다** — 그건 응답 전체를 메모리에 읽는다
    (`net._body`). 실측 476MB 짜리를 그렇게 받으면 안 된다. 여기만 `urllib`
    을 직접 쓰는 이유다.

    `.part` 로 받고 완결되면 rename 한다. 반쯤 받은 파일이 `dest` 이름으로
    보이면 전사가 그걸 집어간다.

    서버가 `Range` 를 무시하고 200 을 주면 **이어붙이지 않고 처음부터** 쓴다 —
    이어붙이면 파일이 조용히 깨진다.
    """
    open_url = open_url or _open_url
    dest = str(dest)
    part = dest + ".part"
    d = os.path.dirname(dest)
    if d:
        os.makedirs(d, exist_ok=True)

    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": "bytes=%d-" % have} if have else {}

    with open_url(url, headers) as r:
        if have and getattr(r, "status", 200) != 206:
            have = 0                      # 서버가 Range 를 무시했다
            headers = {}
        with open(part, "ab" if have else "wb") as f:
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


# ------------------------------------------------------------------ 전사
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
