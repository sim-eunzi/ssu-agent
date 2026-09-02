# ssu-agent — 핸드오프

> 설계·정찰이 끝난 상태의 인수인계 문서.
> 이 문서 하나로 구현을 시작할 수 있어야 한다.
> 작성 2026-09-02 · 대상 2026-2학기 (09-01 ~ 12-28)

---

## 0. 한 줄 요약

숭실대 Canvas/LearningX에서 출석·진도·마감을 읽어, `study.py`로 vault를 갱신하고
텔레그램으로 알리는 개인용 에이전트. iMac(24h 서버)에서 구동.

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

**함정 두 가지**

- 폼 파싱은 반드시 `<input>` 태그 단위로. `name="..." value="..."` 정규식은
  중간에 `id="..."`가 끼어 실패한다 (실제로 500 발생함).
- `oauth_nonce`/`oauth_timestamp`는 일회용. 1~3을 한 흐름에서 즉시 처리할 것.

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
| 텔레그램 양방향(버튼 체크) | 같은 봇 토큰 2프로세스 폴링 시 업데이트 경합. 쓰기 경로는 LMS 하나로 | — |
| 헤르메스봇에 통합 | 읽기 전용이면 새 봇이 더 쌈. 잘 도는 코드 건드리는 비용이 더 큼 | — |
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

### M1 — 마감·진도 알림 (최소 동작)

1. `canvas.py` — 인증 체인(§2.1), JWT 캐시(2h)
2. `sync.py` — `lessons` + `attendance_items` 수집
3. `risk.py` — 잔여량 ÷ 가용시간 (평일 1.5h, 주말 4h). `duration` 실측 사용
4. `notify.py` — 텔레그램 **발신 전용** 새 봇
   - 07:30 아침 / 21:30 저녁 / 토 10:00 주간
   - 즉시 알림 3종만: 마감 앞당겨짐 · D-3 이내 신규 · 휴강/시험 공지
   - 알림에 `html_url` 첨부 → 탭 한 번에 해당 강의 진입
5. `state/notified.json` 중복 방지

폴링: `lessons` 30분 / `attendance_items` 하루 3회 (N+1이라 7과목×약40 = 280회)

### M2 — vault 반영
`study.py` 서브프로세스 호출. **선행 조건: §7 보강 필요**

### M3 — 자료·요약
Commons 다운로드 → `materials/`. mp4 → MLX Whisper → `summary.md`

### M4 — 시험
기존 `coach-curriculum` 스킬로 study-coach 업로드

---

## 7. study.py 보강 명세 (M2 선행 조건)

현재 명령으로는 에이전트 갱신을 표현할 수 없다. **구현 전 별도 판정 필요.**

| 필요 | 현재 문제 | 근거 |
|---|---|---|
| `add` 멱등 | 존재 검사 없이 `lines.insert` → 폴링마다 행 무한 증식 | `study.py:479-508` |
| 주차 정렬 삽입 | 표 마지막에 삽입 → 3주차 퀴즈가 15주차 아래로 | `:498-501`, `page.tsx:49` |
| `--json` 조회 | `list` 출력이 사람용 포맷. 기계 판독 불가 | `:344` |

기존 호출자(코코봇 `univ-save`, 아침 체크인 cron, 주간 세팅 cron)에
영향 없음을 확인할 것.

---

## 8. 환경

```bash
CANVAS_BASE=https://canvas.ssu.ac.kr
CANVAS_TOKEN=      # 개인 액세스 토큰. 만료 2026-12-31
TELEGRAM_TOKEN=    # 신규 봇 (@BotFather)
TELEGRAM_CHAT_ID=
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

1. Commons CDN 직링크가 인증 없이 받아지는지 (`curl -I`)
2. `attendance_items` 조회 시 아이템 22/52가 비었던 이유 (퀴즈·과제는 콘텐츠 없음일 가능성)
3. `study.py` 보강 3건의 `/feature` 판정
4. `note.md` 편집용 대시보드 UI — 별도 작업 (Next.js, `data/` 경로 읽기)
