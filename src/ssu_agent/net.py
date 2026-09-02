"""stdlib 전용 HTTP 헬퍼.

외부 의존성을 두지 않는 것이 의도다. iMac 에 venv 를 만들지 않고
`python3 bin/...` 로 바로 돌리기 위해서다.
"""

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

TIMEOUT = 30


class HttpError(Exception):
    def __init__(self, code, url, body=""):
        self.code = code
        self.url = url
        self.body = body
        super().__init__("HTTP {} {} {}".format(code, url, body[:200]))


def _body(resp):
    raw = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw


def request(url, method="GET", headers=None, data=None, opener=None,
            timeout=TIMEOUT, retries=2, backoff=1.5):
    """(status, headers, bytes) 반환. 5xx/네트워크 오류만 재시도.

    retries=0 은 '절대 재시도 금지'. LTI 폼 POST 처럼 nonce 가 일회용인
    요청에 반드시 쓴다.
    """
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    opener = opener or urllib.request.build_opener()

    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.headers, _body(r)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = _body(e).decode("utf-8", "replace")
            except Exception:
                pass
            last = HttpError(e.code, url, body)
            if e.code < 500 or attempt == retries:
                raise last
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = HttpError(0, url, str(e))
            if attempt == retries:
                raise last
        time.sleep(backoff * (attempt + 1))
    raise last


def get_json(url, headers=None, opener=None, **kw):
    _, _, raw = request(url, headers=headers, opener=opener, **kw)
    text = raw.decode("utf-8", "replace")
    # Canvas 는 일부 응답에 while(1); 프리픽스를 붙인다.
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]
    return json.loads(text) if text.strip() else None


def get_text(url, headers=None, opener=None, **kw):
    _, _, raw = request(url, headers=headers, opener=opener, **kw)
    return raw.decode("utf-8", "replace")


def post_form(url, fields, headers=None, opener=None, **kw):
    body = urllib.parse.urlencode(fields).encode()
    hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        hdrs.update(headers)
    return request(url, method="POST", headers=hdrs, data=body,
                   opener=opener, **kw)


def next_link(headers):
    """Canvas Link 헤더에서 rel="next" URL 을 뽑는다."""
    link = headers.get("Link") or headers.get("link")
    if not link:
        return None
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) < 2:
            continue
        url = seg[0].strip().strip("<>")
        for attr in seg[1:]:
            if attr.strip().lower() in ('rel="next"', "rel=next"):
                return url
    return None


def cookie_opener():
    jar = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return op, jar


class FormParser(HTMLParser):
    """첫 <form> 의 action 과 모든 <input> 의 name/value.

    정규식으로 name/value 를 긁으면 중간에 낀 id="..." 때문에 값이 어긋나
    LTI POST 가 500 난다 (핸드오프 §2.1 함정 1). HTMLParser 는 태그 단위로
    속성을 주고 엔티티도 알아서 푼다.
    """

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and self.action is None:
            self.action = a.get("action")
        elif tag == "input" and self.action is not None:
            name = a.get("name")
            if name:
                self.fields[name] = a.get("value") or ""


def parse_lti_form(html_text):
    p = FormParser()
    p.feed(html_text)
    if not p.action:
        raise ValueError("LTI 폼을 찾지 못했다 (action 없음)")
    return p.action, p.fields
