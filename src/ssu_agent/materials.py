"""Commons 강의자료 다운로드 (M3).

`sync` 가 이미 모은 `attendance_items` 만 쓴다 — 새 조회가 없다.

**한 번도 안 연 강의의 자료는 못 받는다.** 미개봉 항목은 404 라
`item_content_data` 자체가 오지 않아 `content_type`·`view_url` 이 없다
(핸드오프 §2.2). 그래서 이 모듈은 개봉분만 처리하고, 주차가 풀려 은지가
열면 다음 실행에서 자연히 채워진다. 추정하지 않는다.

동영상은 받지 않는다 (`movie`·`everlec`). 용량이 크고 보관이 목적이 아니다.
"""

import hashlib
import html as html_mod
import json
import os
import re
import tempfile
from datetime import datetime

from . import net
from .config import DATA_DIR, KST

COMMONS = "https://commons.ssu.ac.kr"
CONTENT_PHP = COMMONS + "/viewer/ssplayer/uniplayer_support/content.php?content_id=%s"
EM = COMMONS + "/em/%s"

VIEW_RE = re.compile(r"/em/([0-9a-zA-Z]+)")
URI_RE = re.compile(
    r"<content_download_uri>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content_download_uri>",
    re.S)


# ------------------------------------------------------------------ 파싱
def content_id_of(item):
    m = VIEW_RE.search(item.get("view_url") or "")
    return m.group(1) if m else None


def parse_download_uri(xml):
    """`&amp;` 를 풀지 않으면 URL 이 깨진다 — 실측에서 확인."""
    m = URI_RE.search(xml or "")
    if not m:
        return None
    return html_mod.unescape(m.group(1)).strip() or None


def safe_name(name, content_id):
    """경로 조작을 막고 확장자를 맞춘다. 한글 파일명을 그대로 살린다."""
    base = (name or "").replace("\\", "/").split("/")[-1].strip().lstrip(".")
    if not base:
        base = content_id
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


# ------------------------------------------------------------------ 계획
def plan(snap):
    jobs = []
    for _cid, e in sorted((snap.get("courses") or {}).items()):
        for it in e.get("items") or []:
            if it.get("kind") != "lecture" or it.get("unopened"):
                continue
            if (it.get("content_type") or "").lower() != "pdf":
                continue
            cid, wk = content_id_of(it), it.get("week")
            if not cid or wk is None:
                continue
            jobs.append({"stem": e.get("stem"), "week": int(wk),
                         "content_id": cid,
                         "file": safe_name(it.get("file_name"), cid),
                         "size": it.get("total_file_size"),
                         "title": it.get("title")})
    return jobs


def not_ready(snap):
    """PDF 인데 아직 못 받는 것. **실패가 아니다.**

    `item_content_data.content_id` 가 `'not_open'` 이면 파일은 올라와 있어도
    뷰어 URL 이 안 나온다 — 교수가 콘텐츠를 아직 공개하지 않은 상태다.
    미개봉(404)과는 다른 축이다: 항목은 열렸지만 콘텐츠가 잠겨 있다.
    실측 2026-09-02 — 개봉 PDF 19개 중 14개가 여기 해당했다.
    주차가 풀리면 다음 실행에서 자연히 받힌다.
    """
    out = []
    for _cid, e in sorted((snap.get("courses") or {}).items()):
        for it in e.get("items") or []:
            if it.get("kind") != "lecture" or it.get("unopened"):
                continue
            if (it.get("content_type") or "").lower() != "pdf":
                continue
            if content_id_of(it):
                continue
            out.append({"stem": e.get("stem"), "week": it.get("week"),
                        "file": it.get("file_name") or "(이름 없음)",
                        "size": it.get("total_file_size")})
    return out


def week_dir(semester, stem, week):
    return DATA_DIR / semester / stem / ("W%02d" % int(week))


def load_meta(d):
    try:
        got = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": {}}
    got.setdefault("items", {})
    return got


def save_meta(d, meta):
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, str(d / "meta.json"))


def already_have(job, meta, have_file):
    """장부에 있고 파일도 실제로 있어야 건너뛴다.
    materials/ 는 gitignore 라 다른 기기에서는 장부만 있고 파일이 없을 수 있다."""
    return bool((meta.get("items") or {}).get(job["content_id"])) and have_file


# ------------------------------------------------------------------ 실행
def fetch_xml(content_id, request=None):
    request = (request or net.request)(
        CONTENT_PHP % content_id, headers={"Referer": EM % content_id})
    return request[2].decode("utf-8", "replace")


def fetch_file(uri, content_id, request=None):
    url = uri if uri.startswith("http") else COMMONS + uri
    return (request or net.request)(
        url, headers={"Referer": EM % content_id})[2]


def run(snap, semester, dry_run=False, request=None, log=print):
    jobs = plan(snap)
    res = {"saved": 0, "skipped": 0, "failed": 0, "bytes": 0}
    for job in jobs:
        d = week_dir(semester, job["stem"], job["week"])
        meta = load_meta(d)
        dest = d / "materials" / job["file"]
        if already_have(job, meta, dest.exists()):
            res["skipped"] += 1
            continue
        if dry_run:
            log("  + %s W%02d %s" % (job["stem"], job["week"], job["file"]))
            res["saved"] += 1
            continue
        try:
            uri = parse_download_uri(fetch_xml(job["content_id"], request))
            if not uri:
                raise ValueError("content_download_uri 없음")
            blob = fetch_file(uri, job["content_id"], request)
            if not blob.startswith(b"%PDF"):
                raise ValueError("PDF 가 아니다 (로그인 페이지로 튕겼을 수 있다)")
        except Exception as e:                      # 한 건 실패가 전체를 멈추지 않는다
            log("  ✖ %s W%02d %s — %s" % (job["stem"], job["week"], job["file"], e))
            res["failed"] += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        meta["items"][job["content_id"]] = {
            "file": job["file"], "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "title": job["title"], "source": EM % job["content_id"],
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds")}
        meta["week"] = job["week"]
        save_meta(d, meta)
        res["saved"] += 1
        res["bytes"] += len(blob)
        log("  ✓ %s W%02d %s (%.1fMB)"
            % (job["stem"], job["week"], job["file"], len(blob) / 1048576.0))
    return res
