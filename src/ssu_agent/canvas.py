"""Canvas API + LearningX JWT 발급 체인.

핸드오프 §2.1 을 그대로 구현한다.

    Canvas 토큰 → sessionless_launch → LTI 폼 POST → xn_api_token(JWT)

JWT 는 exp=iat+7200 이라 2 시간짜리다. state/jwt.json 에 캐시하고
남은 시간이 SKEW 미만이면 재발급한다. 브라우저·SSO 비밀번호는 쓰지 않는다.
"""

import base64
import json
import re
import threading
import time
import urllib.parse

from . import net, state
from .config import get

ATTENDANCE_RE = re.compile(r"/learningx/lti/lecture_attendance/items/view/(\d+)")
JWT_CACHE = "jwt.json"
SKEW = 300          # 남은 수명이 5분 미만이면 새로 받는다


# ---------------------------------------------------------------- Canvas
class Canvas:
    def __init__(self, cfg=None):
        self.cfg = cfg or get()
        if not self.cfg.canvas_token:
            raise RuntimeError("CANVAS_TOKEN 이 없다. .env 를 확인하라.")
        self._h = {"Authorization": "Bearer " + self.cfg.canvas_token,
                   "Accept": "application/json"}

    def get(self, path, **params):
        url = self.cfg.canvas_base + path
        if params:
            url += ("&" if "?" in path else "?") + _qs(params)
        return net.get_json(url, headers=self._h)

    def paged(self, path, **params):
        """Link 헤더를 따라 전부 모은다."""
        params.setdefault("per_page", 100)
        url = self.cfg.canvas_base + path
        url += ("&" if "?" in path else "?") + _qs(params)
        out = []
        while url:
            _, headers, raw = net.request(url, headers=self._h)
            text = raw.decode("utf-8", "replace")
            if text.startswith("while(1);"):
                text = text[9:]
            chunk = json.loads(text) if text.strip() else []
            if isinstance(chunk, list):
                out.extend(chunk)
            else:
                return chunk
            url = net.next_link(headers)
        return out

    # 자주 쓰는 것들 -----------------------------------------------------
    def courses(self):
        return self.paged("/api/v1/courses", enrollment_state="active")

    def modules(self, cid):
        return self.paged("/api/v1/courses/{}/modules".format(cid),
                          **{"include[]": "items"})

    def announcements(self, cid, start_date=None):
        params = {"context_codes[]": "course_{}".format(cid)}
        if start_date:
            params["start_date"] = start_date
        return self.paged("/api/v1/announcements", **params)


def _qs(params):
    parts = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            parts.extend((k, str(x)) for x in v)
        else:
            parts.append((k, str(v)))
    return urllib.parse.urlencode(parts)


# ------------------------------------------------------------ LearningX
def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


class LearningX:
    """JWT 하나로 전 과목을 읽는다 (핸드오프 §2.1)."""

    def __init__(self, canvas=None, cfg=None):
        self.cfg = cfg or get()
        self.canvas = canvas or Canvas(self.cfg)
        self._jwt = None
        self._lock = threading.Lock()   # 스레드풀에서 401 재발급이 겹치지 않게

    # --- 런치 씨앗 ------------------------------------------------------
    def find_seed(self, refresh=False):
        """런치에 쓸 출석 아이템 하나를 찾는다.

        모듈 아이템의 external_url 이 lecture_attendance/items/view/{id} 인
        것만 고른다. 같은 ExternalTool 이어도 tool 41 은 Q&A 게시판이라
        JWT 가 나오지 않는다.

        launch_api_url 은 아이템의 url 필드를 **그대로** 쓴다. tool id 와
        인코딩된 inner url 이 이미 박혀 있어서, 우리가 다시 조립하면
        이중 인코딩(%253A)으로 500 이 난다. 과목마다 tool id 가 다를 수
        있다는 문제도 같이 사라진다.
        """
        cached = state.load("seed.json")
        if not refresh and cached.get("launch_api_url"):
            return cached

        for cid in self.cfg.course_ids:
            try:
                for m in self.canvas.modules(cid):
                    for it in m.get("items") or []:
                        hit = ATTENDANCE_RE.search(it.get("external_url") or "")
                        if hit and it.get("url"):
                            seed = {
                                "course_id": cid,
                                "item_id": int(hit.group(1)),
                                "launch_api_url": it["url"],
                                "found_at": state.now().isoformat(timespec="seconds"),
                            }
                            state.save("seed.json", seed)
                            return seed
            except net.HttpError:
                continue
        raise RuntimeError("출석 아이템을 가진 과목을 찾지 못했다 "
                           "(학기 초라 콘텐츠가 아직 안 열렸을 수 있다)")

    # --- JWT ------------------------------------------------------------
    def jwt(self, force=False):
        with self._lock:
            return self._jwt_locked(force)

    def _jwt_locked(self, force):
        if self._jwt and not force and jwt_exp(self._jwt) - time.time() > SKEW:
            return self._jwt
        if not force:
            cached = state.load(JWT_CACHE).get("token")
            if cached and jwt_exp(cached) - time.time() > SKEW:
                self._jwt = cached
                return cached
        self._jwt = self._launch()
        state.save(JWT_CACHE, {"token": self._jwt, "exp": jwt_exp(self._jwt)})
        return self._jwt

    def _launch(self):
        """sessionless_launch → 폼 → POST → Set-Cookie: xn_api_token.

        oauth_nonce/oauth_timestamp 는 일회용이다. 1~3 을 끊지 않고 잇는다.
        """
        base = self.cfg.canvas_base
        seed = self.find_seed()
        try:
            launch = net.get_json(seed["launch_api_url"], headers=self.canvas._h)["url"]
        except net.HttpError:
            # 아이템이 사라졌거나 재편성됐다. 씨앗을 다시 찾는다.
            seed = self.find_seed(refresh=True)
            launch = net.get_json(seed["launch_api_url"], headers=self.canvas._h)["url"]

        opener, jar = net.cookie_opener()
        page = net.get_text(launch, opener=opener)
        action, fields = net.parse_lti_form(page)

        # 재시도 금지: nonce 가 소모된다.
        net.post_form(action, fields, opener=opener, retries=0, headers={
            "Referer": base + "/",     # 없으면 500
            "Origin": base,
        })

        for c in jar:
            if c.name == "xn_api_token" and c.value:
                return c.value
        raise RuntimeError("LTI 런치는 통과했으나 xn_api_token 쿠키가 없다")

    # --- API ------------------------------------------------------------
    def get(self, path, retry_auth=True):
        headers = {"Authorization": "Bearer " + self.jwt(),
                   "Accept": "application/json",
                   "X-User-Locale": "ko"}
        try:
            return net.get_json(self.cfg.canvas_base + path, headers=headers)
        except net.HttpError as e:
            if e.code in (401, 403) and retry_auth:
                self.jwt(force=True)
                return self.get(path, retry_auth=False)
            raise

    def lessons(self, cid):
        return self.get("/learningx/api/v1/courses/{}/lessons".format(cid))

    def attendance_item(self, cid, item_id):
        return self.get(
            "/learningx/api/v1/courses/{}/attendance_items/{}".format(cid, item_id))
