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

import re

from . import materials

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
