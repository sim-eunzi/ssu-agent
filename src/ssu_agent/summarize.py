"""자료 → 마크다운 → LLM 요약 (M3 후반).

추출만 갈리고 요약은 하나다.

    PDF ── pymupdf4llm ──┐
                         ├─→ markdown/{파일}.md ─→ LLM ─→ summary.md
    mp4 ── faster-whisper ┘        (원본 보존)      (공통)

## 🔴 재개가 설계의 중심이다

긴 자료는 청크로 나눠 부르고, **어디까지 했는지 `.progress/` 에 남긴다.**
상한에 걸리든 에러가 나든 다음 실행이 그 다음 청크부터 이어받는다.
마크다운도 보존하므로 PDF 를 다시 파싱하지 않는다.

## 의존성은 여기서만 쓴다

`pymupdf4llm` 과 LLM SDK 는 **함수 안에서 import** 한다. ssu-agent 의 나머지
(sync·vault-sync·materials·brief)는 의존성 0 을 그대로 유지한다.

LLM 은 갈아끼울 수 있게 한 함수에 가둬 뒀다 — `run(llm=...)` 로 주입한다.
그래서 테스트 전부가 키도 네트워크도 없이 돈다.

## 스캔 PDF 는 실패가 아니다

실측 2026-09-02 — 4차산업혁명과창업 자료 2개(127·133쪽)가 텍스트 **0자**였다.
스캔 이미지라 텍스트 레이어가 없다. `unsupported_scanned` 로 기록하고 넘어간다.
Claude 비전으로 읽는 길은 있으나 건당 $1~2 라 지금은 열지 않는다.
"""

import json
import os
import re
import time
from datetime import datetime

from .config import DATA_DIR, KST

PROVIDER = os.environ.get("SUMMARY_PROVIDER", "anthropic")   # anthropic | openai
_DEFAULT_MODEL = {"anthropic": "claude-opus-5", "openai": "gpt-5.1"}
MODEL = os.environ.get("SUMMARY_MODEL") or _DEFAULT_MODEL.get(PROVIDER, "claude-opus-5")
EFFORT = os.environ.get("SUMMARY_EFFORT", "medium")   # gpt-5 계열 reasoning_effort
CHUNK_CHARS = int(os.environ.get("SUMMARY_CHUNK_CHARS", "40000"))
MAX_CALLS = int(os.environ.get("SUMMARY_MAX_CALLS", "30"))   # 실행당 LLM 호출 상한

# 추정용. 청구서가 아니라 눈대중이다 — 모델을 바꾸면 이 값도 바꿔야 한다.
_DEFAULT_USD = {"anthropic": (5.0, 25.0), "openai": (1.25, 10.0)}   # Opus 5 / gpt-5.1
_ui, _uo = _DEFAULT_USD.get(PROVIDER, (5.0, 25.0))
USD_IN = float(os.environ.get("SUMMARY_USD_IN", _ui)) / 1_000_000
USD_OUT = float(os.environ.get("SUMMARY_USD_OUT", _uo)) / 1_000_000
# 2026-09-02 실측으로 보정 — 3문서 8,027자가 입력 8,019토큰이었다.
# 한글은 거의 1자=1토큰이다(처음엔 2.0 으로 잡아 절반을 밑돌았다).
CHARS_PER_TOKEN = 1.0
EST_OUT_TOKENS = 1600        # 실측 3회 4,690토큰 → 회당 ~1,563

# 실제 청구 토큰. call_openai 가 채우고 run 이 장부에 옮긴다 — 어림이 아니라 실측이다.
# 주입한 가짜 llm 은 건드리지 않으므로 테스트에서는 0 으로 남는다.
LAST_USAGE = {}


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


# ------------------------------------------------------------------ 추출
def extract_pdf(path):
    """(마크다운, {pages}). pymupdf4llm 은 여기서만 import 한다."""
    try:
        import pymupdf
        import pymupdf4llm
    except ImportError as e:
        raise RuntimeError("pymupdf4llm 이 없다: pip3 install pymupdf4llm") from e
    doc = pymupdf.open(str(path))
    pages = doc.page_count
    doc.close()
    return pymupdf4llm.to_markdown(str(path), show_progress=False), {"pages": pages}


def looks_scanned(md, pages):
    """텍스트 레이어가 없는 PDF 인가. 쪽수 대비 글자가 너무 적으면 그렇다."""
    n = len((md or "").strip())
    if n == 0:
        return True
    return n < max(40, int(pages) * 20)


# ------------------------------------------------------------------ 청킹
def chunk(md, size=CHUNK_CHARS):
    """문단 경계를 우선하되, 한 문단이 통째로 크면 잘라서라도 나눈다."""
    md = md or ""
    if len(md) <= size:
        return [md]
    out, cur = [], ""
    for para in md.split("\n\n"):
        while len(para) > size:                 # 문단 하나가 너무 크면 강제 분할
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:size])
            para = para[size:]
        if not cur:
            cur = para
        elif len(cur) + 2 + len(para) <= size:
            cur += "\n\n" + para
        else:
            out.append(cur)
            cur = para
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ 장부
def progress_dir(week_dir):
    return week_dir / ".progress"


def load_progress(week_dir, content_id):
    p = progress_dir(week_dir) / ("%s.json" % content_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_progress(week_dir, content_id, rec):
    d = progress_dir(week_dir)
    d.mkdir(parents=True, exist_ok=True)
    rec = dict(rec, updated_at=now())
    (d / ("%s.json" % content_id)).write_text(
        json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return rec


# ------------------------------------------------------------------ LLM
SYSTEM = (
    "너는 대학생의 강의자료를 정리한다. 원문에 없는 사실을 만들지 마라. "
    "슬라이드에서 뽑은 텍스트라 띄어쓰기가 깨져 있을 수 있으니 문맥으로 읽어라."
)
PROMPT_ONE = """다음은 「{course}」 {week}주차 자료 「{file}」 에서 뽑은 마크다운이다.

아래 형식으로 한국어 정리를 만들어라.

### 한 줄
### 핵심 개념 (3~6개, 각각 한 줄 설명)
### 시험에 나올 만한 것
### 모르는 채로 남는 것 — 자료만으로 안 풀리는 질문

---
{body}"""
PROMPT_PART = """다음은 「{course}」 {week}주차 자료 「{file}」 의 {i}/{n} 부분이다.
이 부분에서 나온 것만 불릿으로 뽑아라. 형식은 자유롭게, 사실만.

---
{body}"""
PROMPT_MERGE = """아래는 「{course}」 {week}주차 자료 「{file}」 를 {n}조각으로 나눠
각각 뽑은 메모다. 중복을 합쳐 하나로 정리해라.

### 한 줄
### 핵심 개념 (3~6개, 각각 한 줄 설명)
### 시험에 나올 만한 것
### 모르는 채로 남는 것

---
{body}"""


def _anthropic():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic 이 없다: pip3 install anthropic") from e
    return anthropic


def _openai():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai 가 없다: pip3 install openai") from e
    return OpenAI


def list_models():
    """이 키로 볼 수 있는 모델. SUMMARY_MODEL 을 고를 때 쓴다."""
    if PROVIDER == "openai":
        return sorted(m.id for m in _openai()().models.list().data)
    return sorted(m.id for m in _anthropic().Anthropic().models.list().data)


def _call_anthropic(prompt, system, model, effort, max_tokens):
    client = _anthropic().Anthropic()
    kw = dict(model=model, max_tokens=max_tokens, system=system,
              messages=[{"role": "user", "content": prompt}],
              thinking={"type": "adaptive"}, output_config={"effort": effort})
    try:
        r = client.messages.create(**kw)
    except TypeError:                        # 구버전 SDK 는 thinking/output_config 를 모른다
        kw.pop("thinking", None)
        kw.pop("output_config", None)
        r = client.messages.create(**kw)
    if getattr(r, "stop_reason", None) == "refusal":
        raise RuntimeError("모델이 거절했다: %s" % getattr(r, "stop_details", None))
    u = getattr(r, "usage", None)
    if u is not None:
        LAST_USAGE.update(input=getattr(u, "input_tokens", 0),
                          output=getattr(u, "output_tokens", 0),
                          model=getattr(r, "model", model))
    return "\n".join(b.text for b in r.content if b.type == "text").strip()


def _call_openai(prompt, system, model, effort, max_tokens):
    client = _openai()()
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": prompt}]
    kw = dict(model=model, messages=msgs,
              max_completion_tokens=max_tokens, reasoning_effort=effort)
    for _ in range(2):
        try:
            r = client.chat.completions.create(**kw)
            break
        except TypeError:
            kw.pop("reasoning_effort", None)
        except Exception as e:
            m = str(e)
            if "reasoning_effort" in m or "max_completion_tokens" in m:
                kw.pop("reasoning_effort", None)
                kw["max_tokens"] = kw.pop("max_completion_tokens", max_tokens)
                continue
            raise
    else:
        raise RuntimeError("LLM 호출이 두 번 다 실패했다")
    u = getattr(r, "usage", None)
    if u is not None:
        LAST_USAGE.update(input=getattr(u, "prompt_tokens", 0),
                          output=getattr(u, "completion_tokens", 0),
                          model=getattr(r, "model", model))
    return (r.choices[0].message.content or "").strip()


def call_llm(prompt, system=SYSTEM, model=None, effort=None, max_tokens=4000):
    """공급자는 SUMMARY_PROVIDER 로 고른다 — 둘 다 공식 SDK 를 쓴다.

    갈아끼울 수 있게 한 함수에 가둬 뒀다. 이 세션에서 두 번 갈아탔다:
    Claude → (은지 키가 OpenAI 라) OpenAI → (다시) Claude.
    """
    LAST_USAGE.clear()
    fn = _call_openai if PROVIDER == "openai" else _call_anthropic
    try:
        return fn(prompt, system, model or MODEL, effort or EFFORT, max_tokens)
    except Exception as e:
        m = str(e)
        if "model" in m and ("not exist" in m or "not_found" in m or "not found" in m):
            raise RuntimeError(
                "모델 '%s' 을 못 찾는다. `ssu-agent summarize --models` 로 확인하고 "
                ".env 의 SUMMARY_MODEL 을 고쳐라" % (model or MODEL)) from e
        raise


# ------------------------------------------------------------------ 대상
def _targets(semester, root=None):
    """(주차디렉터리, content_id, 파일명) — 받아둔 PDF 전부."""
    base = (root or DATA_DIR) / semester
    out = []
    if not base.is_dir():
        return out
    for course in sorted(p for p in base.iterdir() if p.is_dir()):
        for wd in sorted(p for p in course.iterdir() if p.is_dir()):
            try:
                meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for cid, rec in sorted((meta.get("items") or {}).items()):
                f = rec.get("file") or ""
                if f.lower().endswith(".pdf") and (wd / "materials" / f).exists():
                    out.append((wd, cid, f, meta))
    return out


def _markdown_path(week_dir, filename):
    return week_dir / "markdown" / (re.sub(r"\.pdf$", "", filename, flags=re.I) + ".md")


def _get_markdown(week_dir, filename, extract):
    """이미 뽑아둔 게 있으면 재사용한다 — 재개할 때 PDF 를 다시 파싱하지 않는다."""
    mp = _markdown_path(week_dir, filename)
    if mp.exists():
        return mp.read_text(encoding="utf-8"), None
    md, info = extract(week_dir / "materials" / filename)
    return md, info


def _write_section(week_dir, filename, body):
    """summary.md 에 자료별 섹션. 이미 있으면 갈아끼운다 (중복 방지)."""
    sp = week_dir / "summary.md"
    head = "## %s" % filename
    old = sp.read_text(encoding="utf-8") if sp.exists() else ""
    block = "%s\n\n%s\n" % (head, body.strip())
    pat = re.compile(r"^## %s\n.*?(?=^## |\Z)" % re.escape(filename), re.S | re.M)
    new = pat.sub(block + "\n", old) if pat.search(old) else (old.rstrip() + "\n\n" + block if old.strip() else block)
    sp.write_text(new.lstrip("\n"), encoding="utf-8")


# ------------------------------------------------------------------ 실행
def _add_usage(res, rec):
    """직전 호출의 실제 토큰을 실행 합계와 문서 장부에 더한다."""
    u = dict(LAST_USAGE)
    LAST_USAGE.clear()
    if not u:
        return
    res["in_tokens"] = res.get("in_tokens", 0) + u.get("input", 0)
    res["out_tokens"] = res.get("out_tokens", 0) + u.get("output", 0)
    d = rec.setdefault("usage", {"input": 0, "output": 0})
    d["input"] += u.get("input", 0)
    d["output"] += u.get("output", 0)
    rec["model_used"] = u.get("model")


def run(semester, extract=extract_pdf, llm=call_llm, chunk_size=CHUNK_CHARS,
        max_calls=MAX_CALLS, root=None, log=print):
    res = {"done": 0, "skipped": 0, "failed": 0, "unsupported": 0,
           "calls": 0, "budget_hit": False, "in_tokens": 0, "out_tokens": 0}
    for wd, cid, fname, meta in _targets(semester, root):
        pr = load_progress(wd, cid)
        if pr.get("status") in ("done", "unsupported_scanned"):
            res["skipped"] += 1
            continue
        if res["calls"] >= max_calls:
            res["budget_hit"] = True
            break

        ctx = dict(course=meta.get("course", wd.parent.name),
                   week=meta.get("week", wd.name), file=fname)
        try:
            md, info = _get_markdown(wd, fname, extract)
        except Exception as e:                      # 추출 실패는 문서 하나만 막는다
            log("  ✖ %s 추출 실패 — %s" % (fname, e))
            save_progress(wd, cid, dict(pr, file=fname, status="failed",
                                        last_error=str(e)[:300]))
            res["failed"] += 1
            continue

        if info and looks_scanned(md, info.get("pages", 1)):
            log("  ⊘ %s — 스캔 PDF (텍스트 레이어 없음)" % fname)
            save_progress(wd, cid, {"file": fname, "status": "unsupported_scanned",
                                    "pages": info.get("pages"),
                                    "note": "이미지 PDF. 텍스트 추출 불가"})
            res["unsupported"] += 1
            continue

        mp = _markdown_path(wd, fname)
        if not mp.exists():
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(md, encoding="utf-8")

        chunks = chunk(md, chunk_size)
        partials = list(pr.get("partials") or [])
        rec = dict(pr, file=fname, status="in_progress", model=MODEL,
                   chunks_total=len(chunks), chunks_done=len(partials),
                   partials=partials, started_at=pr.get("started_at") or now())
        save_progress(wd, cid, rec)

        try:
            # ── 청크별 부분 요약 (이미 한 건 건너뛴다)
            while len(partials) < len(chunks):
                if res["calls"] >= max_calls:
                    res["budget_hit"] = True
                    break
                i = len(partials)
                body = chunks[i]
                prompt = (PROMPT_ONE if len(chunks) == 1 else PROMPT_PART).format(
                    i=i + 1, n=len(chunks), body=body, **ctx)
                partials.append(llm(prompt))
                res["calls"] += 1
                _add_usage(res, rec)
                rec = save_progress(wd, cid, dict(rec, partials=partials,
                                                  chunks_done=len(partials)))
            if len(partials) < len(chunks):
                log("  ⏸ %s — %d/%d 청크. 다음 실행에서 이어간다"
                    % (fname, len(partials), len(chunks)))
                break

            # ── 합치기 (청크가 하나면 그대로)
            if len(chunks) == 1:
                final = partials[0]
            else:
                if res["calls"] >= max_calls:
                    res["budget_hit"] = True
                    log("  ⏸ %s — 합치기만 남았다. 다음 실행에서" % fname)
                    break
                final = llm(PROMPT_MERGE.format(n=len(chunks),
                                                body="\n\n---\n\n".join(partials), **ctx))
                res["calls"] += 1
                _add_usage(res, rec)
        except Exception as e:
            log("  ✖ %s — %s" % (fname, e))
            save_progress(wd, cid, dict(rec, status="failed", last_error=str(e)[:300]))
            res["failed"] += 1
            continue

        _write_section(wd, fname, final)
        save_progress(wd, cid, dict(rec, status="done", partials=partials,
                                    chunks_done=len(chunks), finished_at=now()))
        res["done"] += 1
        log("  ✓ %s (%d청크)" % (fname, len(chunks)))
    return res


def estimate(semester, extract=extract_pdf, chunk_size=CHUNK_CHARS, root=None):
    """키 없이 도는 눈대중. 실제 청구서가 아니다."""
    e = {"docs": 0, "unsupported": 0, "skipped": 0, "chars": 0, "chunks": 0}
    for wd, cid, fname, _meta in _targets(semester, root):
        if load_progress(wd, cid).get("status") in ("done", "unsupported_scanned"):
            e["skipped"] += 1
            continue
        try:
            md, info = _get_markdown(wd, fname, extract)
        except Exception:
            continue
        if info and looks_scanned(md, info.get("pages", 1)):
            e["unsupported"] += 1
            continue
        cs = chunk(md, chunk_size)
        e["docs"] += 1
        e["chars"] += len(md)
        e["chunks"] += len(cs) + (1 if len(cs) > 1 else 0)   # 합치기 호출 포함
    e["est_input_tokens"] = int(e["chars"] / CHARS_PER_TOKEN)
    e["est_output_tokens"] = e["chunks"] * EST_OUT_TOKENS
    e["est_usd"] = round(e["est_input_tokens"] * USD_IN
                         + e["est_output_tokens"] * USD_OUT, 3)
    return e
