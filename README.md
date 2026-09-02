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
| **M2** vault 반영 | ⛔ 차단 — [핸드오프 §7](docs/HANDOFF-ssu-agent.md) 판정 선행. **iMac 에서만 가능** |
| **M3** 자료·요약 | 미착수 |
| **M4** 시험 | 미착수 |

M1 실측: 인증 1.4초 · 7과목 237항목 수집 14.5초 · 테스트 46건.

**다음은 iMac 에서 이어야 한다.** `study.py` 도 vault 도 헤르메스봇도
이 저장소를 만든 회사 맥북에는 없다. 핸드오프 §0.5 에 넘길 목록이 있다.

## 지금 되는 것 — M1

| 모듈 | 하는 일 |
|---|---|
| `net.py` | stdlib HTTP. LTI 폼 파서, Canvas Link 페이지네이션 |
| `canvas.py` | Canvas API + LTI 인증 체인 → LearningX JWT (2h 캐시) |
| `sync.py` | 모듈·lessons·attendance_items·공지 수집 → `state/snapshot.json` |
| `risk.py` | 잔여 영상 시간 ÷ 실제 가용 시간 → 🟢🟡🟠🔴 |
| `events.py` | 스냅샷 diff → 마감 앞당겨짐 / D-3 신규 / 휴강·시험 공지 |
| `brief.py` | 텍스트/JSON 렌더. 전송 없음 |

## 쓰기

```bash
cp .env.example .env      # CANVAS_TOKEN 채우기
./bin/ssu-agent doctor    # 환경 점검
./bin/ssu-agent auth      # 인증 체인 실측
./bin/ssu-agent sync      # 수집 → state/snapshot.json
./bin/ssu-agent items 선형대수
./bin/ssu-agent brief morning
```

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

```cron
*/30 7-23  * * *  cd ~/ssu-agent && ./bin/ssu-agent sync >> state/cron.log 2>&1
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

- **M2** vault 반영 — `study.py` 보강 3건 판정이 선행 (핸드오프 §7)
- **M3** Commons 자료 다운로드 · Whisper 요약
- **M4** study-coach 업로드
- `progress` 필드 스케일 실측 — 아직 시청 이력이 0 이라 못 했다 (§10-5)
- `late_at`(지각 마감) 미사용 (§10-6)

## 데이터

`docs/samples/` 는 정찰 원본이다. 테스트 픽스처로 쓴다.
**실명·학번·Canvas user_id 는 마스킹했다.** 토큰은 애초에 들어간 적 없다.
