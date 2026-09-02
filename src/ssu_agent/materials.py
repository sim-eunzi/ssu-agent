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


# 받을 수 있는 형식. 나머지는 not_ready 가 이유와 함께 기록한다.
DOWNLOADABLE = ("pdf",)
# 애초에 자료가 아닌 것 — 셀 이유가 없다.
IGNORED_TYPES = ("movie", "everlec")


SKIP_NOTES = {
    "not_open": "교수가 콘텐츠를 아직 공개 안 함. 주차가 풀리면 다음 실행에서 받힌다. "
                "할 일 없음.",
    "unsupported_type": "PDF 가 아니라 Commons 뷰어(content.php)가 "
                        "'Not Supported Content Type' 을 돌려준다 — "
                        "content_download_uri 자체가 없어 **받는 것부터 막힌다**. "
                        "PPT→PDF 변환은 그 다음 문제다. 뚫으려면 Canvas Files API "
                        "정찰 + LibreOffice(soffice) 설치가 필요하다. "
                        "실측 2026-09-02 표본 1건(선형대수 W1 강의자료ppt.zip)이라 "
                        "지금은 기록만 한다. 주차가 열려 건수를 보고 정한다.",
}


def write_skipped(snap, path):
    """못 받은 자료를 사유별로 파일에 남긴다.

    stdout 은 cron 로그로 흘러가 묻힌다. 다음 세션이 "무엇이 왜 빠졌나"를
    다시 정찰하지 않으려면 파일이어야 한다. `summarize` 의 재개 장부와 같은 뜻이다.
    """
    items = not_ready(snap)
    counts = {}
    for x in items:
        counts[x["reason"]] = counts.get(x["reason"], 0) + 1
    doc = {"fetched_at": snap.get("fetched_at"),
           "counts": counts, "items": items,
           "notes": {r: SKIP_NOTES.get(r, "") for r in counts}}
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
    return doc


def not_ready(snap):
    """자료인데 아직 못 받는 것. **실패가 아니다.** 이유를 같이 남긴다.

    `not_open` — `item_content_data.content_id` 가 `'not_open'` 이면 파일은
    올라와 있어도 뷰어 URL 이 안 나온다. 교수가 콘텐츠를 아직 공개하지 않은
    상태다. 미개봉(404)과는 다른 축이다 — 항목은 열렸지만 콘텐츠가 잠겨 있다.
    실측 2026-09-02: 개봉 PDF 19개 중 14개. 주차가 풀리면 자연히 받힌다.

    `unsupported_type` — PDF 가 아닌 자료. 실측 2026-09-02 에 선형대수 1주차
    `강의자료ppt.zip`(content_type: `file`) 하나가 있었는데, Commons 뷰어가
    **"Not Supported Content Type"** 을 돌려줘 `content_download_uri` 자체가
    없었다. **PPT→PDF 변환 이전에 받는 것부터 막혀 있다** — 뚫으려면 Canvas
    Files API 정찰이 따로 필요하고 LibreOffice 도 있어야 한다.
    표본이 1건이라 지금은 기록만 한다. 주차가 열려 몇 건인지 보고 정한다.
    """
    out = []
    for _cid, e in sorted((snap.get("courses") or {}).items()):
        for it in e.get("items") or []:
            if it.get("kind") != "lecture" or it.get("unopened"):
                continue
            ct = (it.get("content_type") or "").lower()
            if not ct or ct in IGNORED_TYPES:
                continue
            if ct not in DOWNLOADABLE:
                reason = "unsupported_type"
            elif content_id_of(it):
                continue                    # 받을 수 있다
            else:
                reason = "not_open"
            out.append({"stem": e.get("stem"), "week": it.get("week"),
                        "file": it.get("file_name") or "(이름 없음)",
                        "size": it.get("total_file_size"),
                        "content_type": ct, "reason": reason})
    return out


def week_index(entry):
    """과목 entry → {주차: [링크]}. 대시보드가 읽을 형태로 납작하게."""
    out = {}
    for it in entry.get("items") or []:
        wk = it.get("week")
        if wk is None:
            continue
        try:
            wk = int(wk)
        except (TypeError, ValueError):
            continue
        rec = {"kind": it.get("kind"), "title": it.get("title") or "",
               "url": it.get("html_url") or ""}
        for k in ("content_type", "completed", "unopened", "due_at"):
            if it.get(k) is not None:
                rec[k] = it[k]
        out.setdefault(wk, []).append(rec)
    return out


def write_index(snap, semester, dry_run=False):
    """주차별 링크를 meta.json 에 심는다. **대시보드와의 계약이 여기다.**

    대시보드가 `state/snapshot.json` 을 직접 읽으면 ssu-agent 내부 구조에
    결합된다. meta.json 을 인터페이스로 두면 소스도 하나로 준다 —
    대시보드는 이미 `data/` 에서 자료를 읽어야 한다.

    🔴 자료 장부(`items`)는 보존한다. 덮으면 멱등이 깨져 이미 받은 걸 또 받는다.
    """
    n = 0
    for _cid, e in sorted((snap.get("courses") or {}).items()):
        stem = e.get("stem")
        for wk, links in sorted(week_index(e).items()):
            if dry_run:
                n += 1
                continue
            d = week_dir(semester, stem, wk)
            meta = load_meta(d)
            meta.update(course=stem, week=wk, links=links,
                        updated_at=datetime.now(KST).isoformat(timespec="seconds"))
            save_meta(d, meta)
            n += 1
    return n


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

    `data/` 전체가 gitignore 라 clone 한 기기에는 둘 다 없다. 그래도 장부만
    남고 파일이 지워지는 경우(수동 정리 등)가 있어 파일 존재를 같이 본다."""
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
