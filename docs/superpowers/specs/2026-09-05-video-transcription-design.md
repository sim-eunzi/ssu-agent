# 동영상 강의 전사 → 요약 (설계)

- **날짜** 2026-09-05
- **상태** 승인됨. 구현 착수 전
- **경위** `~/eunzi-os/05_AI/작업중/2026-09-05_동영상전사_설계.md` (정찰·결정 근거)

`materials`(자료 다운로드)·`summarize`(PDF→요약) 다음 조각이다. 동영상 강의를
전사해 텍스트로 남기고, **기존 요약 경로에 그대로 흘려보낸다.**

---

## 1. 문제

README 는 *"`faster-whisper` 전사 → 같은 LLM 경로. **추출만 붙이면 된다**"* 고
적어뒀다. 2026-09-05 정찰에서 **절반만 맞다**는 게 드러났다.

| 확인 | 결과 |
|---|---|
| 영상 취득 | ✅ `content.php` 가 `<media_uri>` 에 CDN 직링크를 준다. **인증 불필요**, `Range` 지원 |
| 자막 트랙 | ❌ 없다. XML 에 caption/vtt/smi 없고 `web_files/*.xml` 도 전부 404 → **전사 필수** |
| 용량 | 한나아렌트 1-2강 58분 = **476MB** |
| 규모 | 열린 것만 영상 **37개 ≈ 36시간**. 학기 전체는 3~4배 |
| 하드웨어 | **Intel i5-7500 (2017) · 4코어 · 16GB · GPU 가속 없음.** x86 이라 MLX 불가 → 순수 CPU |

낙관이 놓친 것: **취득 경로가 아예 없었다.** `materials.py` 의 `IGNORED_TYPES`
에 `movie`·`everlec` 이 들어 있어 의도적으로 제외돼 있었다.

## 2. 결정

1. **범위** — 전 강의 자동 아카이브. 주차가 열리는 대로 전부.
2. **모델** — `small` 로 시작하고 **실측 후 조정.** 추정치로 고정하지 않는다.
3. **실행 창** — 03:00 잡, 실행당 시간 상한.
4. **비용** — 전사는 로컬($0), LLM 은 **요약에만.**
5. **재개 단위** — **파일.** 세그먼트 재개는 실측 전에 사지 않는다.

## 3. 구조

```
movie item ──content.php──> <media_uri> (CDN mp4)
     │
     ├─ 다운로드 → state/tmp/{content_id}.mp4      ← 전사 끝나면 삭제
     │
     └─ faster-whisper(small, int8) ─────┐
                                          ▼
                          markdown/{제목}.md  ← 전사 원본, 영구 보존
                                          │
                     기존 경로 그대로 ─────┘──> LLM ──> summary.md
```

🔴 **이음매가 이미 있다.** `summarize.run(extract=...)` 는 주입 가능하고
`_get_markdown()` 은 `markdown/{이름}.md` 가 있으면 재사용한다. 전사본을 거기에
떨구면 **LLM·청킹·재개 장부를 한 줄도 안 건드린다.**

### 새 모듈 — `transcribe.py`

| 함수 | 책임 |
|---|---|
| `plan(snap)` | 스냅샷에서 전사 대상 고르기. `kind == "lecture"` · `content_type in ("movie","everlec")` · `unopened` 아님 · `week` 있음 · `view_url` 있음 |
| `parse_media_uri(xml)` | `content.php` 응답에서 `<media_uri>` 추출 (순수 함수) |
| `download(url, dest, ...)` | `Range` 이어받기. `.part` 로 받고 완결 시 rename |
| `transcribe_file(path, model)` | faster-whisper 호출 → 세그먼트 목록. **`faster_whisper` 는 이 함수 안에서만 import** |
| `to_markdown(segments, meta)` | `[MM:SS] 문장` 줄들 + 앞머리 메타 |
| `run(semester, ...)` | 위를 엮고 예산·장부·정리를 진다 |

전사본을 쓴 뒤 `meta.json` 의 `items[{content_id}]` 에 **`file: "{안전제목}.md"`**
로 등록한다. `summarize._targets` 가 이 값을 보고 전사본을 집으므로 **둘은 계약이다** —
확장자를 바꾸면 요약이 조용히 안 돈다.

### `summarize.py` 변경 (3곳)

1. `_targets` — `f.lower().endswith(".pdf")` and `materials/{f}` 존재 →
   **전사본은 `markdown/{f}` 존재로 판정**하는 분기 추가
2. `_markdown_path` — 확장자 제거를 `\.(pdf|md)$` 로
3. 전사본은 `_get_markdown` 이 캐시를 반환하므로 `info is None` →
   `looks_scanned` 가 **자동으로 안 탄다** (스캔 PDF 판별이 전사본에 오작동하지 않음)

### `materials.py` 변경

- `IGNORED_TYPES` 에서 `movie`·`everlec` 제거 (더는 "셀 이유 없는 것"이 아니다)
- **`plan()` 은 그대로 PDF 만.** 영상을 `materials/` 에 쌓지 않는다
- `not_ready` 집계에서 영상은 전사 파이프라인 소관임을 명시

## 4. 저장

```
data/{학기}/{과목}/W{주차}/
  markdown/{제목}.md      ← 전사본 (영구)
  summary.md              ← 기존. 전사본 섹션이 추가된다
  meta.json               ← items 에 전사 항목 등록
  .progress/{content_id}.json
state/tmp/{content_id}.mp4  ← 작업 중에만. 성공하면 삭제
```

`data/` 는 gitignore 대상이라 저장소가 붓지 않는다.

전사본 머리말에 출처·길이·모델을 남긴다 — 나중에 모델을 올렸을 때
**무엇을 다시 돌려야 하는지** 알 수 있어야 한다.

```markdown
> 출처 https://commons.ssu.ac.kr/em/{content_id} · 58분 · whisper small(int8) · 2026-09-05
```

## 5. 예산

- **시간** — `TRANSCRIBE_MAX_SECONDS`(기본 10800 = 3시간). **파일 사이에서** 검사.
  상한에 걸리면 `budget_hit` 을 세우고 중단, 다음 새벽이 이어받는다.
- **돈** — 전사 $0. 요약만 과금. 58분 ≈ 2만 자 → **건당 ~$0.14**,
  백로그 37개면 ~$5. 기존 `MAX_CALLS` 가 그대로 상한이다.
- **디스크** — 전사 **성공 시 mp4 즉시 삭제.** 정상 흐름의 점유는 1개 분량(~500MB).
  전사 **실패 시에는 남긴다** — 476MB 를 다시 받는 게 더 비싸다. 대신
  `state/tmp/` 에 총량 상한(`TRANSCRIBE_TMP_MAX_BYTES`, 기본 2GB)을 두고
  넘으면 **오래된 것부터** 지운다. 지워지면 다음 실행이 다시 받을 뿐 손실은 없다.

## 6. 재개와 실패

`.progress/{content_id}.json` 의 `status`:

| status | 뜻 | 다음 실행 |
|---|---|---|
| `done` | 전사본 있음 | 건너뜀 |
| `failed` | 다운로드/전사 실패 | 재시도 (`last_error` 보존) |
| `in_progress` | 죽은 흔적 | **처음부터** 재시도 |

🔴 **재개 단위는 파일이다.** 전사 도중 죽으면 그 파일은 처음부터
(`small` 기준 ~23분 손실). 3시간 창에 7~8개가 들어가고 잘리는 건 항상
마지막 1개라 손실률 15% 미만이다. 세그먼트 재개는 디코드 오프셋·부분 파일·
프롬프트 이어붙이기가 한꺼번에 붙으므로 **실측 전에 사지 않는다.**

한 영상의 실패가 다음 영상을 막지 않는다 — `summarize` 와 같은 규약이다.

## 7. 의존성

`faster_whisper` 는 **함수 안에서만 import.** `pymupdf4llm` 과 같은 규약이라
`sync`·`vault-sync`·`materials`·`brief` 의 **"의존성 0" 은 그대로**다.

시스템 `ffmpeg` 는 설치하지 않는다 — `faster-whisper` 가 번들하는 PyAV 로
디코딩한다. **구현 시 실측 확인이 필요하다**(안 되면 그때 `ffmpeg` 를 논의).

`doctor` 에 설치 여부 한 줄 추가.

## 8. CLI

```bash
ssu-agent transcribe                 # 예산 안에서 돈다
ssu-agent transcribe --dry-run       # 대상만 센다. 네트워크·모델 안 탐
ssu-agent transcribe --limit 1       # 실측용. 1개만
ssu-agent transcribe --model medium  # 모델 덮어쓰기 (기본값은 TRANSCRIBE_MODEL, 미설정 시 small)
```

`refresh` 에는 **아직 안 붙인다.** `refresh` 는 코코봇이 부르는 대화형 입구인데
전사는 시간 단위라 foreground 상한(600초)을 넘는다. 크론 전용으로 시작한다.

## 9. 테스트

네트워크·모델 없이 도는 것이 원칙이다 (기존 166건과 같은 규약).

- `parse_media_uri` — 실제 XML 픽스처(정찰본)로. `media_uri` 없는 응답도
- `plan` — 영상만 고르는가. `unopened`·주차 없음·PDF 를 배제하는가
- `to_markdown` — 타임스탬프 형식, 빈 세그먼트
- `download` — `Range` 이어받기(가짜 HTTP), `.part` → rename
- 예산 — 상한 초과 시 중단하고 `budget_hit`, 장부에 남는가
- 정리 — 성공 시 mp4 삭제 · 실패 시 보존 · `state/tmp/` 총량 상한 초과 시 오래된 것부터 삭제
- `summarize._targets` — 전사본 `.md` 를 잡는가, `looks_scanned` 를 안 타는가
- 통합 — 가짜 전사기 주입해 mp4 → markdown → summary.md 전 구간

## 10. 첫 걸음은 실측

구현 직후 **가장 먼저** 영상 1개를 `--limit 1` 로 돌려 잰다:

1. 실제 전사 속도 (× 실시간)
2. 한국어 품질 — 전문용어·인명이 얼마나 살아남는가
3. PyAV 로 디코딩이 되는가 (`ffmpeg` 없이)
4. 요약 결과가 PDF 요약만큼 쓸 만한가

**이 숫자를 보고 `small` 유지 / `medium` 승격을 정한 다음** 크론에 붙인다.
추정치로 크론을 배선하지 않는다.

## 11. 안 하는 것

- **세그먼트 단위 재개** — 위 참조. 실측 후 필요하면
- **화자 분리·슬라이드 OCR** — 요약 품질에 기여가 불확실하다
- **`refresh` 배선** — foreground 상한 초과
- **영상 보관** — 목적은 텍스트다
