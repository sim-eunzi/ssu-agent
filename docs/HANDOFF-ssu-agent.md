# ssu-agent — 핸드오프

> 설계·정찰이 끝난 상태의 인수인계 문서.
> 이 문서 하나로 구현을 시작할 수 있어야 한다.
> 작성 2026-09-02 · 대상 2026-2학기 (09-01 ~ 12-28)

---

## 0. 한 줄 요약

숭실대 Canvas/LearningX에서 출석·진도·마감을 읽어, `study.py`로 vault를 갱신하고
텔레그램으로 알리는 개인용 에이전트. iMac(24h 서버)에서 구동.

---

## 0.5 진행 상황 (2026-09-02 갱신)

| 마일스톤 | 상태 |
|---|---|
| **M1** 수집·판단 | ✅ 구현·실측 완료. 회사 맥북에서 7과목 실제 데이터로 검증 |
| **M2** vault 반영 | ⛔ **차단됨** — §7 study.py 보강 3건 판정이 선행. iMac 에서만 가능 |
| **M3** 자료·요약 | 미착수 |
| **M4** 시험 | 미착수 |

### M1 에서 실제로 돌아가는 것

```bash
./bin/ssu-agent doctor          # 환경 점검
./bin/ssu-agent auth            # LTI 체인 → JWT (실측 1.4초, 만료 119분)
./bin/ssu-agent sync            # 7과목 237항목 수집 (실측 14.5초)
./bin/ssu-agent items 선형대수   # 남은 항목
./bin/ssu-agent brief --json    # 헤르메스봇이 받아갈 구조
```

의존성 0. 시스템 `python3` 로 돈다. 테스트 46건, 네트워크 없이 픽스처로.

### 실측이 뒤집은 것 (설계 대비)

1. **`attendance_items` 는 개인 열람 기록이다** — §2.2. 안 연 항목은 404.
   237개 중 156개가 404였다. §10 열린항목 2번의 답.
2. **`sessionless_launch` URL 을 직접 조립하면 500** — §2.1. 모듈 아이템의
   `url` 필드를 그대로 써야 한다.
3. **알림 전송을 ssu-agent 에서 걷어냈다** — §3.2, §4. iMac 에 이미 봇이 있다.
   판단은 여기, 전달은 헤르메스봇. 인터페이스는 `brief --json`.

### 이 맥북(회사)에서 못 한 것 — iMac 에서 이어야 함

| 할 일 | 왜 여기서 못 하나 |
|---|---|
| §7 study.py 보강 3건 판정 | `study.py` 가 이 기기에 없다 (iMac `~/eunzi-tools/bin/`) |
| M2 구현 | vault 가 없다. `study.py` 호출이 안 된다 |
| 헤르메스봇 배선 (`brief --json` 호출) | 봇 코드가 이 기기에 없다 |
| cron 등록 | 구동 기기가 iMac 이다 (§8) |
| 실제 진도 검증 (`progress` 스케일) | 아직 아무 강의도 시청 전이라 값이 전부 0 |

**`ssu-agent` repo 에 remote 가 아직 없다.** iMac 으로 옮길 때 정한다.

---

## 1. 사용자 맥락

직장인 학생. 10과목 20학점 중 8과목이 사전녹화 비동기.
근무 중 LMS를 매일 확인하기 어렵고, 과목마다 마감이 전부 다름.

**핵심 문제는 정보 부족이 아니라 확인하러 가는 행위 자체.**
그리고 사전녹화 과목의 진짜 리스크는 마감일이 아니라 **잔여량** —
"내일 마감"은 이미 늦고, "일요일까지 6개 = 4.5시간"을 미리 알아야 한다.

---

## 2. 확정된 기술 사실 (정찰 완료 · 전부 실측)

### 2.1 인증 체인 ★ 가장 중요

Canvas 개인 액세스 토큰은 만료 없음. LearningX JWT는 2시간.
**JWT는 아래 체인으로 무한 재발급 가능.** 브라우저·SSO 비밀번호 불필요.

```
1) GET /api/v1/courses/{cid}/external_tools/sessionless_launch?id={tool}&url={encoded_lti_url}
     Header: Authorization: Bearer {CANVAS_TOKEN}
     → {"id":73, "name":"강의/출결", "url": "<서명된 launch URL>"}
     ※ 이 URL 은 직접 조립하지 말고 모듈 아이템의 `url` 필드를 그대로 쓴다.
       tool id 와 인코딩된 inner url 이 이미 박혀 있다. 다시 quote 하면
       이중 인코딩(%253A)으로 500 이 난다 (2026-09-02 실측).

2) GET <launch URL>
     → HTML. <form action="..."> 안에 hidden input 47개 (oauth_* 서명 포함)

3) POST <form action> with 47 fields
     Header: Content-Type: application/x-www-form-urlencoded
             Referer: https://canvas.ssu.ac.kr/     ← 없으면 500
             Origin:  https://canvas.ssu.ac.kr
             User-Agent: (브라우저 UA)
     → Set-Cookie: xn_api_token=<JWT>   (exp = iat + 7200)

4) LearningX API 호출: Authorization: Bearer {JWT}
```

**함정 세 가지**

- 폼 파싱은 반드시 `<input>` 태그 단위로. `name="..." value="..."` 정규식은
  중간에 `id="..."`가 끼어 실패한다 (실제로 500 발생함).
- `oauth_nonce`/`oauth_timestamp`는 일회용. 1~3을 한 흐름에서 즉시 처리할 것.
  (재시도 로직이 있다면 이 POST 만은 `retries=0` 이어야 한다)
- 1단계 URL 을 직접 만들지 말 것. 위 ※ 참조.

**JWT 하나로 전 과목 접근 가능.** 2시간에 런치 1회면 7과목 전부 커버.
`tool id=73`은 모듈 아이템의 `content_id`에서 얻는다 (과목마다 다를 수 있음).

### 2.2 데이터 소스

| 필요한 것 | 경로 | 인증 |
|---|---|---|
| 과목 목록 | `/api/v1/courses?enrollment_state=active` | Canvas 토큰 |
| 주차 구조·마감 | `/learningx/api/v1/courses/{cid}/lessons` | JWT |
| 진도·출석 | `/learningx/api/v1/courses/{cid}/attendance_items/{item_id}` | JWT |
| 모듈·아이템 목록 | `/api/v1/courses/{cid}/modules?include[]=items` | Canvas 토큰 |
| 강의자료 | `commons.ssu.ac.kr` (아래) | 미확인 |

**`lessons`** — 15주 배열. 각 주차에 `due_at`, `late_at`, `unlock_at`, `lock_at`.
7과목 전부 15건씩 나옴 (Canvas assignments API로는 4과목만 나왔음 — **이쪽이 정본**).
`lessons[].lessons[]`는 수업 일정 메타(class_date, classroom)일 뿐 항목 배열 아님.

**`attendance_items/{id}`** — 아이템 단위. 목록 엔드포인트는 500이라 N+1 불가피.

> **2026-09-02 실측 정정 ★** 이건 과목 콘텐츠 목록이 아니라 **내가 한 번이라도
> 연 항목의 개인 기록**이다. 안 연 항목은 **404** (`opened: true` 필드가 근거).
> 7과목 237개 중 156개가 404였고, 분포가 정확히 열람 이력과 일치했다 —
> 창의융합·선형대수는 0건, 확장현실은 15/15 전부 404.
>
> - 404 는 에러가 아니라 **"안 봤다 = 100% 남았다"는 정보**로 쓴다.
> - 다만 `duration`도 같이 못 얻는다. `lessons`에도 없고, 다른 경로
>   (`/attendance_items/{id}` 무과목, `sections/0/components/{id}/progress`,
>   `components`, `attendances`, `activities`)는 전부 404/400/403/500.
> - 길이를 알려면 LTI 를 런치해야 하는데 그건 열람 기록을 남긴다.
>   `attendance_calc_type`이 열람 기준인 항목이 있을 수 있어 **하지 않는다** (§5).
> - 대신 그 과목의 실측 중앙값으로 추정하고 메시지에 `≈` 로 밝힌다.
>   표본 3개 미만이면 전체 과목 중앙값으로 넘어간다.
>
> `content_type` 은 `movie` / `everlec` / `pdf` / `file` 네 가지가 실측됐다.
> `everlec` 도 `duration` 이 있으므로 영상으로 센다.

```json
{
  "item_id": 909290, "week_position": 1, "lesson_position": 1,
  "title": "1주차 0차시 - ...",
  "due_at": "2026-09-14T14:59:59Z", "late_at": "...", "unlock_at": "...",
  "attendance_data": {
    "completed": false, "attendance_status": "none",
    "progress": 0, "last_at": 0
  },
  "item_content_data": {
    "content_type": "movie",        // movie | pdf
    "duration": 778.9,              // 초. 밀린 양 실측 계산의 근거
    "view_url": "https://commons.ssu.ac.kr/em/{hash}",
    "file_name": "...",             // pdf일 때
    "total_file_size": 92776757
  }
}
```

**강의자료 (Commons)**

```
GET https://commons.ssu.ac.kr/viewer/ssplayer/uniplayer_support/content.php?content_id={hash}
    Referer: https://commons.ssu.ac.kr/em/{hash}
→ XML
```
- pdf: `<content_download_uri>` (상대경로, commons.ssu.ac.kr 기준)
  `web_files/slides/`에 슬라이드 이미지도 있음 → 대시보드 인라인 뷰어에 유용
- movie: `main_media/desktop/html5/media_uri` → CDN 직링크 `.mp4`
- **자막 트랙 없음.** 요약은 mp4 → Whisper 경로 (M3)

미검증: CDN 직링크가 인증 없이 받아지는지 (`curl -I`로 확인 필요)

### 2.3 과목 매핑

Canvas 활성 과목 10개 중 **실제 대상은 7개**. 나머지는 잔여 등록·채플.

| Canvas ID | vault 파일 stem |
|---|---|
| 48130 | `현대사회이슈와기독교` |
| 47738 | `4차산업혁명과창업` |
| 48462 | `선형대수` |
| 47737 | `정치철학-한나아렌트` |
| 47762 | `창의융합인재되기-3code` |
| 49791 | `한반도평화와통일` |
| 48466 | `확장현실디자인` |

**제외**: 18304(설문), 14362(코로나 신고), 48096(비전채플)

**범위 밖 3과목** — Canvas에 없음. 기존 `study.py` 수동 유지, 에이전트 대상 아님.
`kmooc-언어학입문`, `kmooc-현대철학사조`, `카뮈-이방인`

> 과목 키는 **vault 파일 stem**이 1순위. `study.py:352`, `lib/vault.js:523`가
> 둘 다 stem 기준. frontmatter `course:` 값은 폴백.

### 2.4 주차 매핑

Canvas 모듈명이 `1주차`, `2주차`… 로 7과목 전부 정확히 15개. 파싱 예외 없음.

**단, vault 주차와 1:1이 아니다.**
- `정치철학-한나아렌트`는 vault가 1~14 + 16 (15주차 없음)
- K-MOOC은 `week_offset`으로 학기 공통 주차에 맞춰져 있음 (범위 밖이라 무관)

→ 매핑 실패 시 **예외 테이블을 만들지 말고** `study.py`의 `exit 2`를 그대로 받아
알림으로 올린다. 같은 불일치는 `notified.json`으로 1회만 알린다.

---

## 3. 저장소·소유권

### 3.1 배치

```
eunzi-os/10_Univ/2026-2학기/     변경 없음. Ⓐ 단일 영역 유지
                                 출석·진도·마감만. notes/ 만들지 않음
ssu-agent/                       신규 repo (iMac)
├── src/
├── data/2026-2/{과목stem}/W{NN}/
│   ├── note.md          👤 대시보드 UI에서만 편집
│   ├── summary.md       🤖 ssu-agent
│   ├── materials/       🤖 gitignore
│   └── meta.json        🤖
└── state/
    ├── notified.json
    └── last_sync.json
```

**노트를 vault에 두지 않는 것이 이 설계의 핵심 결정.**
`CLAUDE.md §3`("공부 내용은 별도 repo")를 개정하지 않고 그대로 지킨다.
결과적으로 vault 규약 개정이 거의 불필요해졌다.

### 3.2 writer 소유권

| 파일 | writer |
|---|---|
| `{과목}.md` | 운영: `study.py` / 부트스트랩: `seed_univ.py` (학기 1회) |
| `_학기.md` | `seed_univ.py` 전용. **운영 중 writer 없음** — 학사력 변경 시 별도 판정 |
| `note.md` | 사람 — 대시보드 UI에서만 (Obsidian 미사용) |
| `summary.md`, `materials/`, `meta.json` | `ssu-agent` |

**`ssu-agent`는 vault에 직접 쓰지 않는다. `study.py`를 서브프로세스로 호출한다.**

**알림도 직접 보내지 않는다.** 층이 이렇게 나뉜다.

| 층 | 담당 | 이유 |
|---|---|---|
| 수집·판단 (잔여량·마감·사건 감지) | `ssu-agent` | LMS 도메인 지식이 여기 있다. 404 가 "안 봤다"라는 것, `everlec`이 영상이라는 것, 주차 `unlock_at` 폴백 |
| 전달 (텔레그램) | 헤르메스봇 | 이미 아침 체크인을 하고 있다. 전송 경로는 하나 |
| 즉시 알림 (새 공지·새 과제) | Canvas 앱 | 이미 푸시를 준다. 단 **마감 앞당겨짐은 안 알려준다** — 기존 항목의 날짜 변경이라 새 알림이 안 뜬다. 그래서 `events.py` 가 남아 있다 |

인터페이스는 `ssu-agent brief --json` (stdout). 전송에 성공한 쪽이 `--ack` 로
outbox 를 비운다.

---

## 4. 폐기된 안 (같은 길 반복 금지) ★

| 폐기안 | 왜 | 근거 |
|---|---|---|
| `vault.py`로 과목 `.md` 직접 갱신 | `study.py`가 **단일 writer로 선언**돼 있음. 코코봇도 이 CLI를 통과 | `study.py:4-5` |
| 표 전체 재작성 | ✅가 지워짐. 손으로 넣은 각주도 사라짐 | `study.py:28`, `:94-97` |
| Canvas 문자열 그대로 표에 삽입 | `sane()`이 `\|`·개행 거부. 우회하면 대시보드가 행을 조용히 버림 | `study.py:374-382`, `lib/vault.js:430` |
| SQLite 별도 상태 저장소 | 변경 시에만 쓰므로 커밋 노이즈 없음. 이전 스냅샷 = `.md` 자체 | — |
| vault에 `notes/` | 2026-08-26에 이미 폐기된 설계 | 커밋 `b1fdc0f` |
| 새벽 4시 일괄 커밋 / `chore(lms):` / `pull --rebase` | `study.py`는 쓰기마다 즉시 commit+push, 🎓 접두어, `--no-rebase` | `study.py:415-442`, `:508` |
| ssu-agent 가 직접 텔레그램 전송 | **전송 경로가 둘이면 "왜 두 번 왔지"를 디버깅하게 된다.** iMac 에 이미 아침 체크인·알림을 하는 봇이 있다. ssu-agent 는 계산해서 stdout 으로 내놓고 전송은 헤르메스봇이 한다 — `daily_router.py` 가 쓰는 것과 같은 구조 | `daily_router.py:14,432` |
| 텔레그램 양방향(버튼 체크) | 쓰기 경로는 LMS 하나로. ssu-agent 는 애초에 텔레그램을 모른다 | — |
| 헤르메스봇 코드에 통합 | 잘 도는 코드를 건드리는 비용이 더 큼. **판단은 ssu-agent, 전달은 헤르메스봇** — 층이 다르다 | — |
| 헤드리스 브라우저 / SSO 자동 로그인 | §2.1 체인으로 불필요 | — |
| 과목별 주차 오프셋 예외 테이블 | `seed_univ.py`가 이미 `week_offset` 보유 → drift 지점 생김 | — |
| K-MOOC 어댑터 | 범위 밖으로 확정 | — |

---

## 5. Non-goals

- **동영상 자동 시청·진도 조작** — 학칙 위반, 학점 취소 리스크.
  조회(`progress` 읽기)와 조작은 완전히 다른 것. 이 시스템은 **알려주는 도구**다.
- 과제 자동 제출 / vault 직접 쓰기 / 새 웹앱 신설 / 헤르메스봇 기존 로직 수정

---

## 6. 마일스톤

### M1 — 마감·진도 알림 (최소 동작) ✅ 2026-09-02 구현

1. `canvas.py` — 인증 체인(§2.1), JWT 캐시(2h)
2. `sync.py` — `lessons` + `attendance_items` 수집
3. `risk.py` — 잔여량 ÷ 가용시간 (평일 1.5h, 주말 4h). `duration` 실측 사용
4. `brief.py` — 텍스트/JSON 렌더. **전송하지 않는다**
   - `brief morning|evening|weekly` 텍스트, `--json` 구조화
   - 감지 3종: 마감 앞당겨짐 · D-3 이내 신규 · 휴강/시험 공지
   - 항목에 `html_url` 첨부 → 탭 한 번에 해당 강의 진입
5. `state/notified.json` 중복 감지 방지 + `state/outbox.json` 미전달분

폴링: `lessons` 30분 / `attendance_items` 하루 3회 (N+1이라 7과목×약40 = 280회)

### M2 — vault 반영 ⛔ 차단
`study.py` 서브프로세스 호출. **선행 조건: §7 판정 — iMac 에서만 가능**

### M3 — 자료·요약
Commons 다운로드 → `materials/`. mp4 → MLX Whisper → `summary.md`

### M4 — 시험
기존 `coach-curriculum` 스킬로 study-coach 업로드

---

## 7. study.py 보강 명세 (M2 선행 조건) ⛔ 판정 대기

`study.py` 는 vault 과목 `.md` 의 **단일 writer** 다 (§4). ssu-agent 도 이
CLI 를 통과해야 한다. 그런데 **현재 명령으로는 에이전트식 갱신을 표현할 수 없다.**

사람이 하루 한 번 손으로 부르는 것과, 에이전트가 30분마다 부르는 것은
요구가 다르다. 아래 3건이 그 차이다.

### 7.1 세 건이 뭔지

**① `add` 멱등** — `study.py:479-508`

존재 검사 없이 `lines.insert` 한다. 사람이 한 번 부를 땐 문제없지만
ssu-agent 는 30분마다 sync 한다. **같은 행이 폴링 횟수만큼 쌓인다.**
필요한 건 "있으면 갱신, 없으면 삽입"(upsert). 키를 뭘로 잡을지가 판정 포인트 —
`(주차, 항목명)` 인지 `(주차, 유형)` 인지.

**② 주차 정렬 삽입** — `study.py:498-501`, 대시보드 `page.tsx:49`

표 **마지막**에 붙인다. 3주차 퀴즈가 뒤늦게 열리면 15주차 아래로 간다.
대시보드가 표 순서를 그대로 렌더하므로 눈으로 보면 깨져 있다.
정렬 키는 주차 번호지만 **vault 주차가 1:1 이 아니다** — `정치철학-한나아렌트`
는 1~14 + 16 (15주차 없음, §2.4). 정렬이 이걸 견뎌야 한다.

**③ `--json` 조회** — `study.py:344`

`list` 출력이 사람용 포맷이라 기계가 못 읽는다. ssu-agent 가
"지금 vault 에 뭐가 있나"를 알 수 없다.

### 7.2 판정할 것

**의존 관계가 판정의 핵심이다.** ③ 이 있으면 멱등 판단을 ssu-agent 쪽에서
할 수 있다(읽고 → 없는 것만 add). ① 을 `study.py` 안에서 하면 ③ 없이도 된다.
**둘 다 할 필요는 없을 수 있다.**

| 안 | 내용 | 대가 |
|---|---|---|
| A | ① 만 — `study.py` 가 upsert 를 책임진다 | 단일 writer 원칙에 충실. `study.py` 가 커진다 |
| B | ③ 만 — ssu-agent 가 읽고 판단해서 없는 것만 add | `study.py` 를 덜 건드림. 경합 창이 생긴다(읽기~쓰기 사이) |
| C | ①+③ 둘 다 | 안전하지만 가장 비쌈 |

② 는 어느 안이든 필요하다 — 삽입 위치 문제라 밖에서 못 고친다.

### 7.3 착수 전 확인

- **기존 호출자 3곳에 영향 없어야 한다**: 코코봇 `univ-save`,
  아침 체크인 cron, 주간 세팅 cron.
- `study.py` 는 쓰기마다 즉시 commit+push 한다 (`:415-442`, `:508`).
  30분 폴링이 커밋 노이즈를 만들지 확인할 것 — **변경이 있을 때만 써야 한다.**
- `sane()` 이 `|`·개행을 거부한다 (`:374-382`). Canvas 문자열을 그대로
  넣으면 안 된다. 우회하면 대시보드가 행을 조용히 버린다 (`lib/vault.js:430`).

> **이 판정은 iMac 에서 해야 한다.** `study.py` 실물이 거기에만 있다.
> 회사 맥북에는 `~/eunzi-tools/bin/` 에 `study.py` 가 없다 (2026-09-02 확인).

---

## 8. 환경

```bash
CANVAS_BASE=https://canvas.ssu.ac.kr
CANVAS_TOKEN=      # 개인 액세스 토큰. 만료 2026-12-31
# 텔레그램 설정 없음 — ssu-agent 는 전송하지 않는다 (§4)
VAULT_PATH=/Users/eunzi/eunzi-os
STUDY_PY=/Users/eunzi/eunzi-tools/bin/study.py
```

**토큰은 vault에 절대 넣지 않는다** (GitHub 푸시됨).
`.env`는 gitignore. `data/**/materials/`도 gitignore.

구동: iMac (`100.126.98.39`, tailnet). 대시보드 `:3001`, 헤르메스봇과 동거.
`study.py` 호출 때문에 **vault가 있는 기기여야 한다** — 다른 기기 clone 금지 규약.

---

## 9. 참고

- `recon_out/*.json` — 정찰 원본. `docs/samples/`로 옮겨 테스트 픽스처로 사용 권장
- [kdy565/snuETL-mcp](https://github.com/kdy565/snuETL-mcp) — 서울대 eTL(Canvas+LearningX) MCP, MIT.
  같은 벤더라 `canvas_client.py`·`store.py`·`materials.py` 참고 가치 있음

---

## 10. 열린 항목

1. Commons CDN 직링크가 인증 없이 받아지는지 (`curl -I`) — M3
2. ~~`attendance_items` 조회 시 아이템 22/52가 비었던 이유~~
   **해결 (2026-09-02)** — 열람하지 않은 항목이라 404. §2.2 참조.
   남은 문제는 미개봉 항목의 `duration` 을 얻을 방법이 없다는 것.
   교수가 주차별로 콘텐츠를 열어주면 자연히 줄어들 값이라 M1 에선 추정으로 둔다.
3. `study.py` 보강 3건 판정 — §7.2. **iMac 필요**
4. `note.md` 편집용 대시보드 UI — 별도 작업 (Next.js, `data/` 경로 읽기)

**2026-09-02 에 새로 생긴 것**

5. **`progress` 필드의 스케일** (0~1 인지 0~100 인지) 미확인.
   학기 2일차라 시청 이력이 0 이어서 실측이 안 됐다. 강의를 한 편이라도
   보고 나면 `attendance_items` 응답에서 바로 확인된다.
   현재는 `last_at`(초)을 바닥값으로 함께 써서 **과대평가 쪽으로** 안전하게 둔다
   (`risk.py:remaining_seconds`).
6. **`late_at`(지각 마감)을 아직 안 쓴다.** 주차마다 정규 마감과 별개로
   지각 인정 시한이 있다 (1주차: 마감 09/14, 지각 09/21).
   "마감 지남"으로 끝내는 게 맞는지, "지각으로 아직 가능"을 알려줄지 판정 필요.
7. **미개봉 항목 추정치의 정확도.** 지금은 과목 실측 중앙값(표본 3개 미만이면
   전체 중앙값)으로 센다. 주차가 열릴수록 실측이 늘어 자연히 정확해진다.
   빗나가는 폭이 큰지 몇 주 뒤 확인.
8. **`현대사회이슈와기독교`(48130) 는 모듈 15개에 아이템 0개다.**
   교수가 아직 아무것도 안 올렸다. 오류가 아니라 정상이지만, 학기 중반까지
   0 이면 과목 매핑이 틀린 것일 수 있으니 한 번 볼 것.
