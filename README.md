# ssu-agent

숭실대 Canvas/LearningX 에서 출석·진도·마감을 읽어 텔레그램으로 알리는 개인용 에이전트.
설계·정찰 내역은 [docs/HANDOFF-ssu-agent.md](docs/HANDOFF-ssu-agent.md).

**읽기 전용이다.** 동영상 자동 시청·진도 조작·과제 자동 제출은 하지 않는다 (핸드오프 §5).

## 지금 되는 것 — M1

| 모듈 | 하는 일 |
|---|---|
| `net.py` | stdlib HTTP. LTI 폼 파서, Canvas Link 페이지네이션 |
| `canvas.py` | Canvas API + LTI 인증 체인 → LearningX JWT (2h 캐시) |
| `sync.py` | 모듈·lessons·attendance_items·공지 수집 → `state/snapshot.json` |
| `risk.py` | 잔여 영상 시간 ÷ 실제 가용 시간 → 🟢🟡🟠🔴 |
| `events.py` | 스냅샷 diff → 마감 앞당겨짐 / D-3 신규 / 휴강·시험 공지 |
| `notify.py` | 텔레그램 발신 전용. 아침·저녁·주간 브리핑 |

## 쓰기

```bash
cp .env.example .env      # CANVAS_TOKEN, TELEGRAM_* 채우기
./bin/ssu-agent doctor    # 환경 점검
./bin/ssu-agent auth      # 인증 체인 실측
./bin/ssu-agent sync      # 수집 + 즉시 알림
./bin/ssu-agent items 선형대수
./bin/ssu-agent report morning
./bin/ssu-agent notify weekly --refresh
```

`--dry-run` 을 붙이면 발송 대신 표준출력으로 나온다.

의존성 없음. venv 없이 시스템 `python3` 로 돈다 (3.8+).

## 테스트

```bash
python3 -m unittest discover -s tests -t .
```

네트워크를 타지 않는다. `docs/samples/` 의 정찰 원본을 픽스처로 쓴다.

## 배치 (iMac)

`study.py` 를 호출해야 하므로 **vault 가 있는 기기에서만** 돈다. 다른 기기 clone 금지.

```cron
*/30 7-23  * * *  cd ~/ssu-agent && ./bin/ssu-agent sync   >> state/cron.log 2>&1
30    7    * * *  cd ~/ssu-agent && ./bin/ssu-agent notify morning
30    21   * * *  cd ~/ssu-agent && ./bin/ssu-agent notify evening
0     10   * * 6  cd ~/ssu-agent && ./bin/ssu-agent notify weekly
```

`sync` 는 완료된 강의 아이템을 이전 스냅샷에서 재사용해 요청 수를 줄인다.
전부 다시 받으려면 `--full`.

## 설정

`config/courses.json` — 과목 7개(Canvas ID ↔ vault stem), 가용시간, 알림 시각.
과목 키는 **vault 파일 stem** 이 1순위다 (`study.py:352`, `lib/vault.js:523`).

## 아직 안 한 것

- **M2** vault 반영 — `study.py` 보강 3건이 선행 (핸드오프 §7)
- **M3** Commons 자료 다운로드 · Whisper 요약
- **M4** study-coach 업로드
