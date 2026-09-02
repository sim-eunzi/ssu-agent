"""materials.py — Commons 강의자료 다운로드 (M3).

네트워크를 타지 않는다. XML 은 실측 응답을 줄인 것이고,
내려받기는 주입한 가짜 fetch 로 대신한다.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from ssu_agent import materials as m

# 실측 응답(2026-09-02)에서 필요한 부분만. &amp; 가 그대로 들어 있다.
XML = (
    '<?xml version="1.0"?><content version="1.0"><content_playing_info>'
    '<content_id>6a93d5834a3fa</content_id>'
    '<content_type>sharedocs</content_type>'
    '<content_download_uri>/index.php?module=xn_media_content2013'
    '&amp;act=dispXn_media_content2013DownloadWebFile&amp;site_id=ssu1000001'
    '&amp;content_id=6a93d5834a3fa&amp;web_storage_id=371'
    '&amp;file_subpath=contents%5Cweb_files%5Coriginal.pdf</content_download_uri>'
    '</content_playing_info></content>')


def item(week=1, ctype="pdf", vid="6a93d5834a3fa", name="강의노트.pdf",
         size=707787, unopened=False, title="1주차"):
    return {"kind": "lecture", "week": week, "title": title,
            "unopened": unopened, "content_type": ctype,
            "file_name": name, "total_file_size": size,
            "view_url": ("https://commons.ssu.ac.kr/em/" + vid) if vid else ""}


def snap(items, stem="선형대수"):
    return {"semester": "2026-2",
            "courses": {"1": {"canvas_id": 1, "stem": stem, "items": items}}}


class Parsing(unittest.TestCase):
    def test_content_id_from_view_url(self):
        self.assertEqual(m.content_id_of(item()), "6a93d5834a3fa")

    def test_no_view_url_means_no_id(self):
        self.assertIsNone(m.content_id_of(item(vid="")))

    def test_download_uri_unescapes_entities(self):
        uri = m.parse_download_uri(XML)
        self.assertTrue(uri.startswith("/index.php?module="))
        self.assertIn("&act=", uri, "&amp; 를 풀지 않으면 URL 이 깨진다")
        self.assertNotIn("&amp;", uri)

    def test_missing_uri_returns_none(self):
        self.assertIsNone(m.parse_download_uri("<content/>"))


class Naming(unittest.TestCase):
    def test_keeps_original_name(self):
        self.assertEqual(m.safe_name("[학습자료]_창업_0101.pdf", "abc"),
                         "[학습자료]_창업_0101.pdf")

    def test_strips_path_separators(self):
        self.assertEqual(m.safe_name("../../etc/passwd.pdf", "abc"), "passwd.pdf")

    def test_falls_back_to_content_id(self):
        self.assertEqual(m.safe_name("", "6a93d5"), "6a93d5.pdf")

    def test_forces_pdf_extension(self):
        self.assertEqual(m.safe_name("자료", "abc"), "자료.pdf")


class Plan(unittest.TestCase):
    def test_only_pdf(self):
        jobs = m.plan(snap([item(ctype="pdf"), item(week=2, ctype="movie"),
                            item(week=3, ctype="everlec")]))
        self.assertEqual([j["week"] for j in jobs], [1])

    def test_unopened_has_no_material_info(self):
        """404 라 item_content_data 자체가 없다 — 열어야만 받을 수 있다."""
        self.assertEqual(m.plan(snap([item(unopened=True)])), [])

    def test_item_without_content_id_is_dropped(self):
        self.assertEqual(m.plan(snap([item(vid="")])), [])

    def test_carries_course_and_week(self):
        j = m.plan(snap([item(week=7)], stem="확장현실디자인"))[0]
        self.assertEqual((j["stem"], j["week"]), ("확장현실디자인", 7))


class NotReady(unittest.TestCase):
    """실측 — 파일은 올라와 있는데 content_id 가 'not_open' 이라 못 받는다.
    개봉(404)과 다른 축이다: 항목은 열렸지만 콘텐츠가 아직 공개 전.
    19개 중 14개가 이 상태였다 (2026-09-02)."""

    def test_counted_separately_not_as_failure(self):
        s = snap([item(week=1), item(week=2, vid=""), item(week=3, vid="")])
        self.assertEqual(len(m.plan(s)), 1)
        self.assertEqual(len(m.not_ready(s)), 2)

    def test_carries_name_so_the_report_is_useful(self):
        nr = m.not_ready(snap([item(week=5, vid="", name="5주차.pdf")]))
        self.assertEqual((nr[0]["week"], nr[0]["file"]), (5, "5주차.pdf"))

    def test_movies_are_not_counted(self):
        self.assertEqual(m.not_ready(snap([item(ctype="movie", vid="")])), [])

    def test_reason_is_recorded(self):
        nr = m.not_ready(snap([item(week=2, vid="")]))
        self.assertEqual(nr[0]["reason"], "not_open",
                         "파일은 올라왔는데 콘텐츠가 아직 잠긴 것")

    def test_zip_and_ppt_are_recorded_as_unsupported(self):
        """실측 2026-09-02 — 선형대수 1주차 '강의자료ppt.zip'(content_type: file).
        Commons 뷰어가 'Not Supported Content Type' 을 돌려줘 다운로드 경로가
        아예 없다. PPT→PDF 변환 이전에 받는 것부터 막혀 있다."""
        nr = m.not_ready(snap([item(ctype="file", name="강의자료ppt.zip")]))
        self.assertEqual(len(nr), 1)
        self.assertEqual(nr[0]["reason"], "unsupported_type")
        self.assertEqual(nr[0]["content_type"], "file")

    def test_pdf_with_view_url_is_not_listed(self):
        self.assertEqual(m.not_ready(snap([item()])), [], "받을 수 있는 건 안 센다")


class Idempotence(unittest.TestCase):
    def test_known_content_id_is_skipped(self):
        meta = {"items": {"6a93d5834a3fa": {"file": "강의노트.pdf",
                                            "sha256": "x", "size": 1}}}
        job = m.plan(snap([item()]))[0]
        self.assertTrue(m.already_have(job, meta, have_file=True))

    def test_missing_file_forces_redownload(self):
        meta = {"items": {"6a93d5834a3fa": {"file": "강의노트.pdf",
                                            "sha256": "x", "size": 1}}}
        job = m.plan(snap([item()]))[0]
        self.assertFalse(m.already_have(job, meta, have_file=False),
                         "장부에 있어도 파일이 없으면 다시 받는다")

    def test_unknown_content_id_is_downloaded(self):
        job = m.plan(snap([item()]))[0]
        self.assertFalse(m.already_have(job, {"items": {}}, have_file=True))


class Paths(unittest.TestCase):
    def test_layout(self):
        p = m.week_dir("2026-2", "선형대수", 3)
        self.assertEqual(p.parts[-3:], ("2026-2", "선형대수", "W03"))

    def test_week_is_zero_padded(self):
        self.assertTrue(str(m.week_dir("2026-2", "a", 7)).endswith("W07"))


class Run(unittest.TestCase):
    """네트워크 없이 — request 를 주입한다."""

    PDF = b"%PDF-1.7 fake"

    def _req(self, blob=None, xml=XML):
        calls = []

        def request(url, headers=None, **kw):
            calls.append(url)
            body = xml.encode() if "content.php" in url else (
                self.PDF if blob is None else blob)
            return 200, {}, body
        request.calls = calls
        return request

    def _run(self, tmp, **kw):
        with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
            return m.run(snap([item()]), "2026-2", **kw)

    def test_saves_file_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp, request=self._req(), log=lambda *a: None)
            self.assertEqual(res["saved"], 1)
            d = pathlib.Path(tmp) / "2026-2" / "선형대수" / "W01"
            self.assertTrue((d / "materials" / "강의노트.pdf").exists())
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            rec = meta["items"]["6a93d5834a3fa"]
            self.assertEqual(rec["size"], len(self.PDF))
            self.assertEqual(len(rec["sha256"]), 64)
            self.assertIn("fetched_at", rec)

    def test_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, request=self._req(), log=lambda *a: None)
            req2 = self._req()
            res = self._run(tmp, request=req2, log=lambda *a: None)
            self.assertEqual((res["saved"], res["skipped"]), (0, 1))
            self.assertEqual(req2.calls, [], "이미 받은 건 요청조차 하지 않는다")

    def test_html_response_is_rejected(self):
        """로그인 페이지로 튕기면 HTML 이 온다 — PDF 로 저장하면 안 된다."""
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp, request=self._req(blob=b"<!DOCTYPE html>"),
                            log=lambda *a: None)
            self.assertEqual((res["saved"], res["failed"]), (0, 1))
            d = pathlib.Path(tmp) / "2026-2" / "선형대수" / "W01"
            self.assertFalse((d / "materials").exists(), "실패면 파일을 안 남긴다")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = self._req()
            res = self._run(tmp, dry_run=True, request=req, log=lambda *a: None)
            self.assertEqual(res["saved"], 1)
            self.assertEqual(req.calls, [], "dry-run 은 받지도 않는다")
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


class WeekIndex(unittest.TestCase):
    """대시보드가 읽을 계약. snapshot 직독 대신 meta.json 을 인터페이스로 둔다 —
    대시보드가 ssu-agent 내부 구조에 결합되지 않게."""

    def _snap(self, items):
        s = snap(items)
        s["courses"]["1"]["items"] = items
        return s

    def test_makes_meta_for_every_week_with_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
                n = m.write_index(self._snap([item(week=1), item(week=3, vid="")]),
                                  "2026-2")
            self.assertEqual(n, 2)
            for w in ("W01", "W03"):
                self.assertTrue((pathlib.Path(tmp) / "2026-2" / "선형대수" / w
                                 / "meta.json").exists())

    def test_links_carry_url_and_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
                m.write_index(self._snap([dict(item(week=1, title="선대_1_1"),
                                               html_url="https://x/1")]), "2026-2")
                d = json.loads((pathlib.Path(tmp) / "2026-2" / "선형대수" / "W01"
                                / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(d["course"], "선형대수")
            self.assertEqual(d["links"][0]["url"], "https://x/1")
            self.assertEqual(d["links"][0]["title"], "선대_1_1")
            self.assertEqual(d["links"][0]["kind"], "lecture")

    def test_preserves_download_ledger(self):
        """자료 장부를 덮으면 멱등이 깨져 이미 받은 걸 다시 받는다."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
                d = m.week_dir("2026-2", "선형대수", 1)
                m.save_meta(d, {"items": {"abc": {"file": "x.pdf"}}})
                m.write_index(self._snap([item(week=1)]), "2026-2")
                got = m.load_meta(d)
            self.assertIn("abc", got["items"])
            self.assertIn("links", got)

    def test_item_without_week_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
                self.assertEqual(m.write_index(self._snap([item(week=None)]),
                                               "2026-2"), 0)
                self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, "DATA_DIR", pathlib.Path(tmp)):
                m.write_index(self._snap([item(week=1)]), "2026-2", dry_run=True)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class SkippedLedger(unittest.TestCase):
    """못 받은 것은 **파일로** 남아야 다음 세션이 이어받는다.
    stdout 은 cron 로그로 흘러가 버린다."""

    def test_write_skipped_groups_by_reason(self):
        import json, tempfile, os
        snap = {"courses": {"1": {"stem": "선형대수", "items": [
            {"kind": "lecture", "week": 1, "content_type": "file",
             "file_name": "강의자료ppt.zip", "total_file_size": 64238},
            {"kind": "lecture", "week": 2, "content_type": "pdf",
             "file_name": "2주차.pdf",
             "item_content_data": {"content_id": "not_open"}},
        ]}}}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "s.json")
            m.write_skipped(snap, out)
            got = json.loads(open(out, encoding="utf-8").read())
        self.assertEqual(got["counts"], {"not_open": 1, "unsupported_type": 1})
        self.assertIn("fetched_at", got)
        zip_row = [x for x in got["items"] if x["reason"] == "unsupported_type"][0]
        self.assertEqual(zip_row["file"], "강의자료ppt.zip")
        self.assertIn("Commons", got["notes"]["unsupported_type"])

    def test_write_skipped_is_empty_when_all_downloadable(self):
        import json, tempfile, os
        snap = snap_ok = {"courses": {"1": {"stem": "x", "items": [item()]}}}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "s.json")
            m.write_skipped(snap_ok, out)
            got = json.loads(open(out, encoding="utf-8").read())
        self.assertEqual(got["counts"], {})
