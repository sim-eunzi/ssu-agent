"""환경·과목 설정 로딩."""

import json
import os
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "state"
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

KST = timezone(timedelta(hours=9))


def load_dotenv(path=None):
    """.env 를 os.environ 에 주입한다. 이미 있는 환경변수는 덮지 않는다.

    🔴 파일 안에 같은 키가 여러 줄이면 **나중 줄**이 이긴다.
    한 줄씩 setdefault 하면 먼저 나온 줄이 이겨서, 빈 자리표시자 아래에
    진짜 값을 append 했을 때 빈 값이 이긴다 — 2026-09-02 에 두 번 걸렸다
    (CANVAS_TOKEN, 그리고 API 키). append 가 직관대로 동작해야 한다.
    """
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    seen = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        seen[k.strip()] = v.split(" #")[0].strip().strip("'\"")
    for k, v in seen.items():
        os.environ.setdefault(k, v)


class Config:
    def __init__(self):
        load_dotenv()
        self.canvas_base = os.environ.get(
            "CANVAS_BASE", "https://canvas.ssu.ac.kr").rstrip("/")
        self.canvas_token = os.environ.get("CANVAS_TOKEN", "")
        self.vault_path = os.environ.get("VAULT_PATH", "")
        self.study_py = os.environ.get("STUDY_PY", "")

        raw = json.loads((CONFIG_DIR / "courses.json").read_text(encoding="utf-8"))
        self.semester = raw["semester"]
        self.term_start = raw["term_start"]
        self.term_end = raw["term_end"]
        self.courses = raw["courses"]
        self.availability = raw["availability"]
        self.notify = raw["notify"]

        STATE_DIR.mkdir(exist_ok=True)

    # --- 조회 헬퍼 -------------------------------------------------------
    @property
    def course_ids(self):
        return [c["canvas_id"] for c in self.courses]

    def stem(self, canvas_id):
        for c in self.courses:
            if c["canvas_id"] == canvas_id:
                return c["stem"]
        return str(canvas_id)

    def course(self, canvas_id):
        for c in self.courses:
            if c["canvas_id"] == canvas_id:
                return c
        return None

    def missing(self, *keys):
        """비어 있는 필수 환경변수 이름 목록."""
        return [k for k in keys if not getattr(self, k.lower(), "")]


_cfg = None


def get():
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg
