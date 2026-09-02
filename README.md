# ssu-agent

숭실대 Canvas/LearningX 에서 출석·진도·마감을 읽어 텔레그램으로 알리는 개인용 에이전트.
설계·정찰 내역은 [docs/HANDOFF-ssu-agent.md](docs/HANDOFF-ssu-agent.md).

**읽기 전용이다.** 동영상 자동 시청·진도 조작·과제 자동 제출은 하지 않는다 (핸드오프 §5).

**전송도 하지 않는다.** 계산해서 stdout 으로 내놓기만 한다. 텔레그램은
헤르메스봇 하나가 담당한다 — 전송 경로가 둘이면 "왜 두 번 왔지"를
디버깅하게 된다. `eunzi-tools/bin/daily_router.py` 와 같은 구조다.

## 진행 상황

| 마일스톤 | 상태 |
|---|---|
| **M1** 수집·판단 | ✅ 2026-09-02 완료. 7과목 실제 데이터로 검증 |
| **M2** vault 반영 | ✅ **2026-09-02 완료.** `study_cli.py` — 28건 반영·오류 0·2회차 0건 |
| **M3** 자료·요약 | ✅ **2026-09-02.** PDF 5개 102MB + **요약**(PDF→마크다운→LLM). 동영상 전사는 미착수 |
| **대시보드** | ✅ **2026-09-02.** `/univ/{과목}/{주차}` 3분할 — 주차 행·노트·자료/링크 |
| **M4** 시험 | 미착수 |

M1 실측: 인증 1.4초 · 7과목 237항목 수집 14.5초 · 테스트 46건.

**iMac 으로 넘어왔다 (2026-09-02).** `study.py` 보강 5건이 iMac 에서 구현·검증됐고
M2 차단이 풀렸다 — `eunzi-tools` 커밋 `c37f2f4`·`a01fc84`, `study_test.sh` PASS 86.

```bash
study.py list <과목> [주차] --json     # vault 현재 상태 (기계 판독)
study.py add ... --lock-timeout 30    # 멱등 · 주차 정렬 삽입 · 락 대기 초과 시 exit 3
```

`--json` 은 opt-in 이다. 플래그가 없으면 기존 출력이 한 글자도 안 바뀐다 —
아침 체크인 cron 이 `study.py today` 출력을 그대로 붙여넣기 때문이다.

## 지금 되는 것 — M1

| 모듈 | 하는 일 |
|---|---|
| `net.py` | stdlib HTTP. LTI 폼 파서, Canvas Link 페이지네이션 |
| `canvas.py` | Canvas API + LTI 인증 체인 → LearningX JWT (2h 캐시) |
| `sync.py` | 모듈·lessons·attendance_items·공지 수집 → `state/snapshot.json` |
| `risk.py` | 잔여 영상 시간 ÷ 실제 가용 시간 → 🟢🟡🟠🔴 |
| `events.py` | 스냅샷 diff → 마감 앞당겨짐 / D-3 신규 / 휴강·시험 공지 |
| `brief.py` | 텍스트/JSON 렌더. 전송 없음 |
| `study_cli.py` | Canvas 상태 → vault. `study.py` 를 서브프로세스로 (직접 쓰기 없음) |
| `materials.py` | Commons PDF 다운로드 + 주차 인덱스(`meta.json`) |
| `summarize.py` | 자료 → 마크다운 → LLM 요약. **재개 장부**로 중단·에러를 이어받는다 |
| `refresh.py` | 위 넷을 한 번에. **코코봇의 입구** — 순서·중단·보고를 여기가 진다 |

## 쓰기

```bash
cp .env.example .env      # CANVAS_TOKEN 채우기
./bin/ssu-agent doctor    # 환경 점검
./bin/ssu-agent auth      # 인증 체인 실측
./bin/ssu-agent sync      # 수집 → state/snapshot.json
./bin/ssu-agent items 선형대수
./bin/ssu-agent brief morning
./bin/ssu-agent refresh    # 수집→vault→자료→요약 한 번에 (실측 21.7초)
```

### 코코봇이 "최신 LMS 업데이트해줘" 로 부르는 법

```bash
./bin/ssu-agent refresh                  # 전부
./bin/ssu-agent refresh --no-summary     # LLM 안 부름 (비용 0)
./bin/ssu-agent refresh --dry-run        # 확인만. 요약은 estimate 로 빠진다
```

**순서를 스킬에 적지 않는다.** 적으면 스킬과 cron 이 따로 늙는다 — `univ-save`
가 `study.py` 에 로직을 전부 맡긴 것과 같은 규약이다.

🔴 **`sync` 가 문지기다.** 실패하면 뒤를 전부 접고 exit 1 이다 — 낡은 스냅샷으로
vault 를 고치면 마감이 되돌아간다. 나머지 단계는 서로 독립이라 자료 하나가
실패해도 요약은 돈다. 401·연결 실패는 **은지가 읽을 문장으로 번역해서** 낸다.

헤르메스 terminal 의 foreground 상한은 600초다 (`FOREGROUND_MAX_TIMEOUT`).
실측 21.7초라 여유가 있지만, 새 주차가 열려 자료 100MB + 요약이 붙는 날은
`timeout=600` 을 명시해서 부르는 게 안전하다.

의존성 없음. venv 없이 시스템 `python3` 로 돈다 (3.8+).

### 헤르메스봇이 받아가는 법

```bash
./bin/ssu-agent brief --json morning        # 구조화 출력
./bin/ssu-agent brief --json --ack weekly   # 출력 후 outbox 비움
```

`text` 필드는 그대로 전송해도 되게 만들어 뒀다. 마크업은 붙이지 않는다 —
HTML 이냐 Markdown 이냐는 보내는 쪽이 정할 일이다.

`events` 는 "그 사이 바뀐 것"이다. **마감 앞당겨짐**이 여기 들어간다.
Canvas 앱은 이걸 안 알려준다 — 기존 항목의 날짜 변경이라 새 푸시가 안 뜬다.
전송에 성공하면 `--ack` 로 outbox 를 비운다. 실패하면 그대로 두면 다음에 다시 나온다.

## 테스트

```bash
python3 -m unittest discover -s tests -t .
```

네트워크를 타지 않는다. `docs/samples/` 의 정찰 원본을 픽스처로 쓴다.

## 배치 (iMac)

`study.py` 를 호출해야 하므로 **vault 가 있는 기기에서만** 돈다. 다른 기기 clone 금지.

**2026-09-02부터 launchd 로 자동 실행된다.**

| 잡 | 언제 | 하는 일 |
|---|---|---|
| `com.eunzi.ssu-sync` | 30분마다 (07~23시) | `sync` → `vault-sync` |
| `com.eunzi.ssu-materials` | 매일 03:00 | `sync` → `materials` → `summarize` (**비용 발생**) |

래퍼는 `bin/ssu-agent-cron` · `bin/ssu-agent-materials-cron`. 07시 이전이면 그냥
빠져나온다. **`sync` 가 실패하면 `vault-sync` 를 건너뛴다** — 낡은 스냅샷으로
vault 를 고치지 않는다. 로그는 `state/cron.log` (2000줄 회전, git 미추적).

```bash
launchctl list | grep com.eunzi.ssu     # 둘 다 보여야 한다
tail -f state/cron.log
```

브리핑 시각(아침 07:30 / 저녁 21:30 / 토 10:00)은 **헤르메스봇이 정한다.**
헤르메스봇이 `ssu-agent brief --json --ack <kind>` 를 부르면 된다.

`sync` 는 완료된 강의 아이템을 이전 스냅샷에서 재사용해 요청 수를 줄인다.
전부 다시 받으려면 `--full`.

## 설정

`config/courses.json` — 과목 7개(Canvas ID ↔ vault stem), 가용시간, 알림 시각.
과목 키는 **vault 파일 stem** 이 1순위다 (`study.py:352`, `lib/vault.js:523`).

## 실측이 뒤집은 설계

정찰 문서와 달랐던 것 3건. 자세한 건 핸드오프 §2.1 · §2.2 · §3.2.

1. **`attendance_items` 는 과목 콘텐츠가 아니라 개인 열람 기록이다.**
   한 번도 안 연 항목은 404. 237개 중 156개가 404였다.
   404 를 "안 봤다 = 100% 남았다"는 정보로 쓴다. 다만 `duration` 도 같이
   잃어서 과목 중앙값으로 추정하고 `≈` 로 밝힌다.
2. **`sessionless_launch` URL 을 직접 조립하면 500** (이중 인코딩).
   모듈 아이템의 `url` 필드를 그대로 쓴다.
3. **알림 전송을 걷어냈다.** iMac 에 이미 봇이 있다. 판단은 여기, 전달은 저기.

## 아직 안 한 것

- **동영상 요약** — `faster-whisper` 전사 → 같은 LLM 경로. 추출 함수만 붙이면 된다
- **PPT·ZIP** — 실측 2026-09-02, 개봉 자료 81개 중 비(非)PDF 는 **1건**이다
  (선형대수 W1 `강의자료ppt.zip`, `content_type: file`). Commons `content.php` 가
  **`Not Supported Content Type`** 을 돌려줘 `content_download_uri` 가 아예 없다 —
  **변환 이전에 다운로드가 막혀 있다.** 뚫으려면 Canvas Files API 정찰 + LibreOffice
  설치가 필요한데, 표본 1건으로 규칙을 세우면 대개 틀린다. 미개봉 222개가 열려
  건수를 보고 정한다. 사유는 `state/materials_skipped.json` 에 남는다
- **스캔 PDF** — 4차산업 자료 2개는 텍스트 레이어가 없다(`unsupported_scanned`). 비전 모델은 건당 $1~2 라 보류
- **헤르메스봇에 `brief` 위험도 배선** — 마감 요약(`study.py due`)은 2026-09-02 아침 체크인에 붙었다. 밀린 영상 시간은 아직
- **M4** study-coach 업로드
- `progress` 필드 스케일 실측 — 아직 시청 이력이 0 이라 못 했다 (§10-5)
- `late_at`(지각 마감) 미사용 (§10-6)

## 데이터

`docs/samples/` 는 정찰 원본이다. 테스트 픽스처로 쓴다.
**실명·학번·Canvas user_id 는 마스킹했다.** 토큰은 애초에 들어간 적 없다.
