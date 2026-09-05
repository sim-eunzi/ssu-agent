# 요약 입구 재설계 — 현황을 먼저 보여주고, 시킬 때만 돌린다

> 작성 2026-09-06 · 대상 `ssu-agent` · `eunzi-os-dashboard` · `univ-save` 스킬
> 선행 문서: `~/eunzi-os/05_AI/작업중/2026-09-05_손업로드요약_설계.md` (손 업로드 요약, §5 는 (b) 로 확정)
> 관련 문서: `docs/superpowers/specs/2026-09-05-video-transcription-design.md` §8 (상태 뷰·트리거)

## 0. 한 줄

요약을 **자동 파이프라인에 숨은 단계**에서 **사람이 현황을 보고 고르는 명령**으로 옮긴다.
`refresh` 에서 요약을 완전히 빼고, `status` 로 현황을 보여주고, `summarize` 가 무엇을
요약할지는 `sources` 하나로 정한다.

## 1. 왜 — 세 가지가 한 자리에서 만난다

**(a) 손 업로드 PDF 를 요약할 방법이 없다.** 대시보드로 손수 올린 자료는 `meta.json`
장부 밖에 **일부러** 두었다(자동 과금 0 계약). 그래서 03:00 cron 이 안 건드리는데,
사람이 시킬 방법도 같이 없다.

**(b) "요약해줘" 가 무엇을 얼마나 요약할지 아무도 모른다.** `summarize` 는 미요약분을
전부 돈다. 손 업로드 2건을 시켰는데 LMS 미요약 5건이 딸려 들어가고, `--limit` 상한에
걸리면 **정작 시킨 파일이 안 되는** 순서 문제까지 있다.

**(c) 요약 현황을 볼 방법이 없다.** 2026-09-02 에 OpenAI 429 로 5건이 실패했을 때
`state/cron.log` 를 직접 뒤져야 했다. 동영상 전사 스펙 §8.2 가 이미 같은 문제를
지적하고 **통합 상태 뷰**를 결론으로 냈다 — 구현이 Phase 4 로 밀려 있을 뿐이다.

셋의 공통 뿌리는 하나다. **"무엇을 요약할지"와 "얼마나 남았는지"가 코드 어디에도
개념으로 존재하지 않는다.** `_targets` 안에 암묵적으로 있을 뿐이다.

🔴 그리고 넷째가 있다 — **`refresh` 가 돈을 쓴다.** *"최신 LMS 업데이트해줘"* 한 마디에
LLM 비용이 딸려 나가서, 스킬 문서에 *"④ 요약은 돈을 쓴다"* 경고를 달아야 했다.
경고를 달아야 한다는 것 자체가 설계 냄새다.

## 2. 결정

| # | 결정 | 근거 |
|---|---|---|
| 1 | `_targets(semester, root, sources=("ledger",))` — 대상 선정을 **소스 집합**으로 | 불린(`include_manual`)을 하나씩 얹으면 Phase 4 의 `transcript` 에서 조합이 엉킨다 |
| 2 | `status.py` **신설** — 장부만 세는 순수 뷰 | 동영상 스펙 §3 표는 `status` 를 `transcribe.py` 에 넣었지만, 자료 현황을 전사 모듈이 소유하면 계층이 뒤집힌다 |
| 3 | `summarize --manual-only` / `--include-manual` | "올린 것만"을 표현할 방법이 있어야 (b) 의 순서 문제가 사라진다 |
| 4 | 🔴 **`refresh` 에서 요약 단계를 완전히 제거** | 느리고·돈 쓰고·예산을 지는 단계는 동기 대화형 입구에 안 넣는다. 동영상 스펙 §8.5 가 전사를 안 붙인 논리와 같다 |
| 5 | `state/summarize.lock` — `flock` | cron 과 수동이 겹치면 같은 문서를 두 번 요약한다(돈 두 배). 이 설계가 수동 실행을 늘리므로 먼저 닫는다 |
| 6 | 03:00 cron 은 **그대로** `sources=("ledger",)` | 자동 과금 0 계약 유지. 래퍼가 `refresh` 를 거치지 않으므로 결정 4 의 영향도 없다 |

### 2.1 결정 4 가 같이 없애는 것들

`refresh` 에서 요약을 빼면 **요약 때문에 생겼던 특수 규칙이 전부 사라진다.**

| 사라지는 것 | 왜 있었나 |
|---|---|
| `--no-summary` 플래그 | "돈 쓰지 마" 를 표현하려고 |
| `--dry-run` 이 요약만 `estimate` 로 우회하는 규칙 | "확인만 할게" 가 돈을 쓰면 안 돼서 |
| `refresh --limit` | 요약 호출 상한 전달용 |
| 기본값이 두 파서에 갈려 있던 것 | 위 플래그 때문. 2026-09-05 `--limit=None` 사고의 자리다 |
| `_Args(SUMMARIZE_FIELDS, …)` 호출부 | 요약 단계에 넘길 껍데기가 필요해서 |
| 600초 상한 압박 | 자료 100MB + 요약이 붙는 날 몇 분 걸림. 요약을 빼면 실측 21.7초 |

선행 문서 §5 에서 (b)로 정한 `_Args` 방어는 **그대로 넣는다.** 위험한 호출부는
사라지지만 `fresh = _Args(refresh=…, dry_run=…, verbose=…)` 가 남고, 공사는 몇 줄이다.

## 3. `_targets` — 대상 선정 하나로

```python
def _targets(semester, root=None, sources=("ledger",)):
    """(주차디렉터리, content_id, 파일명, meta) — 반환 모양은 지금 그대로.

    sources:
      "ledger"  meta.json items 의 .pdf 이고 materials/{f} 가 있는 것 (지금과 동일)
      "manual"  장부 밖 materials/*.pdf. content_id = "manual-{파일명}"

    모르는 값은 ValueError. Phase 4 가 "transcript" 를 여기 한 줄로 더한다.
    """
```

- **기본값이 `("ledger",)`** — 03:00 cron 은 한 글자도 안 바뀐다
- `content_id` 가 `manual-{파일명}` 인 이유: LMS `content_id` 는 숫자라 절대 안 겹친다.
  `.progress/manual-{파일명}.json` 으로 재개 장부가 그대로 붙는다
- 파일명 비교는 **NFC 정규화 후**. macOS 파일시스템·압축 왕복이 NFD 를 만든다.
  정규화를 빠뜨리면 장부에 있는 파일이 "장부 밖"으로 잘못 갈려 **같은 PDF 를 두 번
  요약한다**(돈 두 배). 대시보드가 같은 이유로 이미 NFC 를 쓴다
- dotfile 제외. `.pdf` 아닌 것 제외 (`pymupdf4llm` 이 pptx·zip 을 못 읽는다)
- `meta.json` 을 못 읽을 때: `"ledger"` 만이면 지금처럼 그 주차를 건너뛴다.
  `"manual"` 이 켜져 있으면 **`.progress` 를 대체 장부로 삼아** materials/ 를 훑는다 —
  파일명이 `manual-` 이 **아닌** 키의 레코드에 `file` 로 올라와 있으면 그건 수집본이다.

  🔴 그냥 "빈 장부"로 보면 안 된다. 장부가 깨진 주차에서 이미 요약된 LMS PDF 들이
  전부 장부 밖으로 보여 `manual-{파일명}` 키로 **다시 요약된다**(같은 문서를 두 번,
  돈 두 배). `.progress` 는 그 파일들을 이미 숫자 `content_id` 로 알고 있으므로
  대체 장부로 충분하다. 실패한 손 업로드는 키가 `manual-…` 이라 이 규칙에 안 걸려
  **재시도가 그대로 된다**
- 주차 안 순서는 ledger → manual 로 결정적. (b)의 순서 문제는 `--manual-only` 가 답이다

## 4. `status.py` — 세기만 한다

```python
def collect(semester, root=None) -> dict   # 순수 함수. 네트워크·LLM 안 탄다
def render(st) -> str                      # 코코봇이 그대로 보내는 텍스트
```

```python
{
  "semester": "2026-2",
  "ledger": {"done": 3, "pending": 5, "failed": 0, "unsupported": 2},
  "manual": {"done": 1, "pending": 2, "failed": 0, "unsupported": 0},
  "video":  None,                       # Phase 4 가 채운다. None 이면 render 가 줄을 안 낸다
  "last_progress_at": "2026-09-05 03:14",   # .progress/*.json 의 updated_at 최대값. 없으면 None
}
```

**카운트는 `_targets(sources=…)` 를 그대로 센다.** status 가 자기 방식으로 `materials/`
를 훑지 않는다 — 화면이 "2건"이라 하고 실행이 3건을 요약하는 어긋남을 막는 근거가
이것 하나다. (`saveMaterial`·`listMaterials`·`why()` 세 군데에 흩어진 장부 계약에서
NFC 를 한쪽만 빠뜨려 버그가 났던 것과 같은 병이다.)

각 대상의 `.progress` 를 읽어 센다:

| `.progress` status | 칸 |
|---|---|
| `done` | done |
| `unsupported_scanned` | unsupported |
| `failed` | failed |
| 없음 · `in_progress` | **pending** |

`in_progress` 를 pending 으로 세는 이유: 중단된 것은 다음 실행이 이어받으므로 "남은 것"이
맞다. 대시보드는 같은 항목을 배지 「요약 중단」으로 따로 보여준다 — 세는 단위와 보여주는
단위가 다른 것은 의도다.

```
📊 요약 현황 (2026-2)
  자동 수집   완료 3 · 미요약 5 · 실패 0 · 스캔불가 2
  직접 올림   완료 1 · 미요약 2 · 실패 0
  마지막 장부 갱신 2026-09-05 03:14
```

- 마크업을 안 붙인다 — HTML 이냐 Markdown 이냐는 보내는 쪽이 정한다 (`brief` 규약)
- `ssu-agent status` · `ssu-agent status --json`
- 🔴 **의도적으로 뺀 것:** 동영상 스펙 §8.2 예시의 *"상한에 걸려 중단(다음 새벽
  이어받음)"* 줄. 장부만 봐서는 알 수 없다. 근거 없는 문장을 화면에 쓰면 그게 다음
  버그다. `마지막 장부 갱신` 은 정직하게 셀 수 있는 값이라 남긴다
- 🔴 **비PDF 손 업로드(pptx·zip)는 status 에 안 잡힌다** — `_targets` 가 안 집기
  때문이다. 대시보드에서만 「요약 미지원」으로 보인다. 셀 수 있는 것만 센다
- 🔴 **`미요약` 은 「받아둔 것 중 안 된 것」이다.** 동영상 스펙 §8.2 예시의
  `대기 14(미공개)` 와 다르다 — 아직 안 받은 자료는 `materials/` 에 없어서 `_targets`
  가 모른다. 세는 근거를 `_targets` 하나로 고정한 대가이고, 그게 화면과 실행이
  안 어긋나는 이유다. 미공개 자료 수는 `materials --dry-run` 의 소관이다

## 5. `refresh` — 요약을 뺀다

```python
STEPS = ("sync", "vault", "materials")
LABEL = {"sync": "① 수집   ", "vault": "② vault  ", "materials": "③ 자료   "}
```

- `--no-summary` · `--limit` 제거. `--no-materials` 는 남는다(이제 materials 만 뺀다)
- `--dry-run` 은 materials 의 dry-run 그대로. 요약 우회 규칙이 사라진다
- `refresh.py` 모듈 docstring 의 *"수집·반영·자료·요약을 한 번에"* 와 *"자료 하나가
  실패해도 요약은 돈다"* 를 고친다
- **`refresh` 는 이제 LLM 을 부르지 않는다** — 네트워크만 쓴다

`cmd_refresh` 가 보고 끝에 현황 한 줄을 덧붙인다. `status.collect` 는 장부만 읽으므로
**공짜**다. `refresh.render` 는 안 건드린다:

```
① 수집   7과목 237항목
② vault  28건 반영
③ 자료   PDF 3개 받음
📊 미요약 — 자동 수집 5건 · 직접 올림 2건   「요약해줘」 라고 하면 골라서 돌린다
```

둘 다 0이면 `📊 미요약 없음`. `sync` 실패로 중단됐으면 현황 줄을 내지 않는다 —
낡은 장부로 센 숫자를 보여주지 않는다.

🔴 **03:00 cron 은 영향 없다.** `bin/ssu-agent-materials-cron` 이 `sync`·`materials`·
`summarize` 를 직접 부르지 `refresh` 를 거치지 않는다. 자동 요약은 그대로 보장된다.
대신 래퍼 마지막에 `ssu-agent status` 한 줄을 찍는다 — 돈 0, 네트워크 0, `cron.log` 에
남아 나중에 브리핑이 읽어갈 자리가 된다.

## 6. 실행 플래그

| 명령 | sources | 누가 부르나 |
|---|---|---|
| `summarize` | `("ledger",)` | 03:00 cron |
| `summarize --include-manual` | `("ledger","manual")` | "자료 전부 요약해줘" |
| `summarize --manual-only` | `("manual",)` | "내가 올린 것만 요약해줘" |

- 두 플래그는 argparse **상호배타 그룹**
- `--estimate` 도 같은 `sources` 를 탄다 — 어림과 실행이 갈리면 어림이 쓸모없다
- `--limit`(기본 `SUMMARY_MAX_CALLS`=30)은 그대로

## 7. 잠금

`state/summarize.lock` 에 `flock(LOCK_EX | LOCK_NB)`.

지금은 잠금이 **없다.** 03:00 cron 과 수동 `summarize` 가 겹치면 A 가 `in_progress` 로
찍어도 B 는 "done 아니네" 하고 **같은 문서를 또 요약한다.** `summary.md` 섹션은 나중에
쓴 쪽이 이긴다. 이 설계는 `status` → 선택 → 실행으로 **수동 실행 빈도를 올리므로**
같이 닫는다. 실제 경로는 새벽 3시가 아니다:

```
은지: "최신 LMS 업데이트해줘"   → refresh
은지: (몇 초 뒤) "요약해줘"      → status → 선택 → summarize
```

- 이미 잡혀 있으면 **실행하지 않고** `"이미 요약이 돌고 있어. 「현황 봐줘」 로 확인해"`
  한 줄을 내고 **exit 0**. 잠금 실패는 에러가 아니다 — cron 은 조용히 빠지고 다음 주기에 온다
- **잠그는 구간은 `run()` 뿐이다.** `--estimate` 와 `--models` 는 장부에도
  `markdown/` 에도 쓰지 않는다(`markdown/{이름}.md` 쓰기는 `run()` 안에서만 일어난다).
  읽기만 하는 명령을 잠그면 "현황 좀 보려다 막히는" 일이 생긴다
- 잠금 파일은 `state/` 에 산다. `state/*` 는 이미 gitignore 이고, `lock.held` 가
  디렉터리를 스스로 만든다 (수동 실행이 cron 래퍼보다 먼저일 수 있다)
- 커널이 자동 해제하므로 프로세스가 죽어도 잠금이 남지 않는다
- 🔴 **공용 `src/ssu_agent/lock.py` 로 만든다.** 동영상 Phase 2 계획의 Task 4 가
  `state/transcribe.lock` 을 만들 예정이다(스펙 §8.4). 같은 규약을 두 번 만들지 않게
  헬퍼를 여기서 내고, Phase 2 계획에 "이 헬퍼를 쓴다" 한 줄을 남긴다

```python
# lock.py
@contextmanager
def held(name, root=None):
    """state/{name}.lock 에 flock. 못 잡으면 acquired=False 로 넘어온다."""
```

## 8. 대시보드

**요약 버튼은 만들지 않는다.** 대시보드는 계속 LLM 을 안 부르고 vault 도 안 쓴다.

`lib/study.js::listMaterials` — 장부 밖 파일도 `.progress` 를 **먼저** 본다:

| 상황 | `summary` | 배지 |
|---|---|---|
| 장부 밖 + `.progress` 있음 | 그 상태 그대로 (`done`/`failed`/`in_progress`/`unsupported_scanned`) | 「요약됨」·「요약 실패」·「요약 중단」·「스캔 PDF」 |
| 장부 밖 + 기록 없음 + `.pdf` | `manual` | **「미요약」** (지금은 「직접 올림」 — 출처만 말할 뿐 다음에 뭘 할 수 있는지 못 알려준다) |
| 장부 밖 + 기록 없음 + 그 외 | `unsupported_manual` | 「요약 미지원」 |
| 장부 안 | 지금과 동일 | 지금과 동일 |

`summaryStatuses()` 는 `rec.file`(실제 파일명)로 매칭하므로 **키 합성 방식과 무관하게
그대로 붙는다** — 손대지 않는다.

`MaterialTabs.why()` 의 "전부 손으로 올린 파일" 분기에 시키는 법을 한 줄 붙인다:
*"코코봇에 「요약해줘」 라고 하면 현황을 보여주고 골라서 돌립니다."*

## 9. 코코봇 (`univ-save` v1.1.0 → v1.2.0)

**스킬은 여전히 로직이 0 이다.** 표에서 명령 하나를 고를 뿐, 두 단계가 됐을 뿐이다.

| 은지가 하는 말 | 명령 |
|---|---|
| "최신 LMS 업데이트해줘" · "학교 자료 받아줘" | `ssu-agent refresh` (**이제 돈을 안 쓴다**) |
| "요약해줘" · "요약 안 된 거 있어?" | ① `ssu-agent status` → 출력 그대로 보여주고 **뭘 돌릴지 묻는다** |
| → "내가 올린 것만" | ② `ssu-agent summarize --manual-only` |
| → "전부" | ② `ssu-agent summarize --include-manual` |
| "얼마 나올지부터" | `... --estimate` 를 먼저 |
| "현황 봐줘" | `ssu-agent status` |

- 🔴 **`refresh` 는 이제 요약을 안 한다.** 스킬의 *"한 번에 넷을 돈다"* → **셋**,
  *"④ 요약은 돈을 쓴다"* 경고는 **삭제**한다
- 🔴 **`summarize` 는 돈을 쓴다.** 애매하면 `--estimate` 를 먼저 돌려 보여주고 묻는다
- **PDF 만 된다.** pptx·zip 은 「요약 미지원」 — 안 되는 걸 되는 것처럼 말하지 마라
- `timeout=600` 명시. 영상 선택지는 Phase 4 전까지 "아직 안 된다"고 답한다

### 9.1 🔴 `description` 을 반드시 고쳐야 한다

라우터는 **스킬 이름과 잘린 `description` 만** 본다 (`skill_utils.py:618`
`extract_skill_description` — 60자 초과면 `desc[:57] + "..."`). 본문 표는 스킬이
**선택된 뒤에야** 읽힌다. 지금 description 에는 `요약` 이라는 말이 없다:

```
학교 수업·과제·퀴즈·마감 기록·조회 + LMS 동기화("최신 LMS 업데이트해줘")
```

**"요약해줘" 는 이 스킬에 도달하지 못한다.** 본문 표에 트리거를 아무리 잘 적어도
소용없다 — 2026-09-02 에 *"최신 LMS 업데이트해줘"* 가 안 먹었던 것과 **정확히 같은
사고**다(그때는 예시가 57자 뒤로 잘렸다).

두 트리거를 모두 남기면서 57자 안에 들어가는 안:

```
학교 수업·마감 기록 + LMS 동기화("업데이트해줘")·자료 요약("요약해줘")
```

- 🔴 **실측으로 확인한다.** 문자 수 계산만으로 끝내지 않는다 — 코코봇에 실제로
  *"요약해줘"* 를 보내 이 스킬이 뽑히는지 본다. 이 규칙 자체가 실측으로 얻어진 것이다

## 10. 시나리오

| # | 시나리오 | 결과 |
|---|---|---|
| 1 | "요약해줘" — 손 업로드만 미요약 | status → ① → `--manual-only` → 그것만 요약. 비용 1건치 |
| 2 | "요약해줘" — LMS 미요약본도 있음 | status 가 **둘 다 세어 보여주고** 은지가 고른다 |
| 3 | "최신 LMS 업데이트해줘" | 수집·vault·자료 3단계 + 현황 줄. **LLM 0** |
| 4 | 03:00 cron | `("ledger",)` 만. 손 업로드 안 봄. 로그에 현황 한 줄 |
| 5 | `refresh --dry-run` | materials dry-run. 요약 우회 규칙 없음 |
| 6 | `refresh --no-materials` | 자료 단계만 제외 |
| 7 | 손 업로드가 스캔 PDF | `unsupported_scanned` → status 「스캔불가」·배지 「스캔 PDF」 |
| 8 | 손 업로드가 pptx·zip | 대상 아님. status 에 안 잡히고 배지만 「요약 미지원」 |
| 9 | 손 업로드 요약 실패(429) | status 「직접 올림 … 실패 1」 로 **보인다**. 자동 재시도는 없다(설계대로) |
| 10 | cron 과 수동이 겹침 | 뒤에 온 쪽이 "이미 돌고 있어" 로 빠진다 (§7) |
| 11 | 두 번째로 또 시킴 | `.progress` done → skipped. 돈 0 |
| 12 | 빈 학기 · 깨진 `meta.json` | status 0 으로 뜨고 안 터진다 |
| 13 | 같은 이름 파일을 지우고 다시 올림 | ⚠️ **범위 밖** — §12 |

## 11. 테스트

**`ssu-agent` (`python3 -m unittest discover -s tests -t .`)**

- `_targets` — sources 4조합(`ledger`/`manual`/둘 다/모르는 값→`ValueError`) · NFC 분해
  파일이 manual 로 새지 않는다 · dotfile·pptx 제외 · 깨진 `meta.json` 에서 손 업로드는
  살아남고 **이미 `.progress` 에 있는 수집본은 manual 로 새지 않는다**(돈 두 배 방지) ·
  깨진 `meta.json` + 실패한 손 업로드는 재시도 대상으로 남는다
- `run`/`estimate` — 기본값이 손 업로드를 안 건드린다 · `--manual-only` 가 장부를 안 건드린다 ·
  손 업로드 `.progress` 의 `file` 이 실제 파일명이다(대시보드 매칭의 계약) · 재실행은 skip
- `status.collect` — 장부·손 업로드·실패·스캔불가 조합 카운트 · `in_progress` 는 pending ·
  빈 학기 · `last_progress_at` 최대값 · `render` 가 `video: None` 이면 줄을 안 낸다
- `refresh` — `STEPS` 가 셋이다 · 요약 단계가 없다 · `sync` 실패 시 중단 규칙 그대로
- CLI — `--manual-only`/`--include-manual` 배타 · 기본이 `("ledger",)` · 잠금이 잡혀 있으면
  실행하지 않고 exit 0
- `lock.held` — 두 번째 획득이 `acquired=False` · 컨텍스트를 빠져나오면 풀린다

**대시보드 (`npm test`)**

- `listMaterials` — manual × `.progress` 조합 4가지 · 비PDF 는 `unsupported_manual` ·
  장부 파일은 아무것도 안 바뀐다 · `meta.json` 을 쓰지 않는다

## 12. 범위 밖 (알고 남기는 것)

- **같은 이름 파일을 지우고 다시 올리면** 옛 `.progress` 가 `done` 이라 건너뛴다.
  업로드 API 가 409 로 덮어쓰기를 막으므로 일부러 지워야 나오는 경로다. 막으려면 손
  업로드에도 해시 장부가 필요한데 그건 **「장부 밖에 둔다」는 계약을 정면으로 뒤집는다.**
  문서에 한 줄 남긴다 — *"같은 이름으로 다시 올리려면 `.progress/manual-{이름}.json` 도 지워라"*
- **ppt/pptx/zip 요약** — `pymupdf4llm` 이 못 읽는다. 실제로 올릴 일이 생기면 그때 정한다
- **대시보드 요약 버튼** — 대시보드는 돈을 안 쓴다
- **출결·강의·공지·퀴즈 현황판** — 재료는 `brief`·`items` 에 이미 있지만 무엇을 몇 줄로
  보여줄지가 별도 결정이다. 다음 스펙
- **영상 칸** — `status` 의 `video` 는 `None` 자리만. Phase 4(스펙 §8.2·§8.3)가 채운다

## 13. 끝나면 갱신할 것

`프로젝트.md`(완료 한 줄, 「대학 · v2 · ssu-agent」 절) · `가이드.md`(트리거) ·
`05_스킬 기능표.md`(§2 대조표 숫자까지) · `01_에르메스 설계.md` §4 ·
`History/2026-09-06_ssu-agent_요약입구.md`(`component: hermes`) · `HISTORY.md` 한 줄 ·
ssu-agent `README.md`(명령·배치 표) · 손 업로드 설계 노트는 이 스펙을 가리키게 ·
동영상 스펙 §3 표(`status`/`pending` → `status.py`)와 §8.2(「직접 올림」 칸) ·
동영상 Phase 2 계획 Task 4 에 "`lock.py` 를 쓴다" 한 줄 · 마지막에 `doctor.sh` **exit 0**
