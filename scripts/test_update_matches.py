"""Tests for update_matches.py (standard library only):  python -m unittest discover -s scripts"""
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_matches as um  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 29, 10, 0, tzinfo=UTC)  # 13:00 in Baghdad


def fd_match(mid, comp, utc, status="TIMED", home="Home", away="Away"):
    return {"id": mid, "utcDate": utc, "status": status,
            "competition": {"code": comp, "name": comp + " league"},
            "homeTeam": {"name": home + " FC", "shortName": home}, "awayTeam": {"name": away, "shortName": away}}


def fd_answer(*matches):
    return {"resultSet": {"competitions": ",".join(sorted({m["competition"]["code"] for m in matches}))},
            "matches": list(matches)}


CHANNEL_MAP = {"channels": {"bein1": {"name": "beIN 1", "epgIds": [], "patterns": ["bein 1"]}},
               "competitions": {"PL": ["bein1"]}}


class WindowTest(unittest.TestCase):
    def test_window_is_baghdad_day(self):
        start, end = um.window(NOW)
        self.assertEqual(start, dt.datetime(2026, 10, 28, 21, 0, tzinfo=UTC))  # 00:00 Baghdad
        self.assertEqual(end - start, dt.timedelta(days=um.DAYS))

    def test_window_just_after_midnight_baghdad(self):
        start, _ = um.window(dt.datetime(2026, 10, 28, 21, 30, tzinfo=UTC))  # 00:30 on the 29th
        self.assertEqual(start.astimezone(um.BAGHDAD).date(), dt.date(2026, 10, 29))

    def test_request_covers_previous_utc_day(self):
        start, end = um.window(NOW)
        self.assertEqual(um.request_dates(start, end), ("2026-10-28", "2026-11-05"))
        span = dt.date.fromisoformat("2026-11-05") - dt.date.fromisoformat("2026-10-28")
        self.assertLessEqual(span.days, 10)  # API maximum range

    def test_early_match_kept_late_dropped(self):
        start, end = um.window(NOW)
        ms = um.from_football_data(fd_answer(
            fd_match(1, "PL", "2026-10-28T21:30:00Z"),   # 00:30 Baghdad today -> kept
            fd_match(2, "PL", "2026-10-28T20:00:00Z"),   # 23:00 Baghdad yesterday -> dropped
        ))
        doc = um.build(ms, CHANNEL_MAP, start, end, NOW)
        self.assertEqual([m["id"] for m in doc["matches"]], ["fd_1"])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.start, self.end = um.window(NOW)

    def test_all_competitions_and_statuses_kept(self):
        ms = um.from_football_data(fd_answer(
            fd_match(1, "SA", "2026-10-29T08:00:00Z", "FINISHED"),
            fd_match(2, "PL", "2026-10-29T09:30:00Z", "IN_PLAY"),
            fd_match(3, "PD", "2026-10-29T18:00:00Z"),
            fd_match(4, "BL1", "2026-10-29T19:00:00Z", "POSTPONED"),
            fd_match(5, "CL", "2026-10-29T19:00:00Z", "WEIRD"),
        ))
        doc = um.build(ms, CHANNEL_MAP, self.start, self.end, NOW)
        um.validate(doc)
        self.assertEqual({m["competitionCode"] for m in doc["matches"]}, {"SA", "PL", "PD", "BL1", "CL"})
        self.assertEqual([m["status"] for m in doc["matches"]], ["FINISHED", "IN_PLAY", "TIMED", "POSTPONED", "SCHEDULED"])

    def test_match_without_channel_is_published(self):
        doc = um.build(um.from_football_data(fd_answer(fd_match(1, "SA", "2026-10-29T18:00:00Z"))),
                       CHANNEL_MAP, self.start, self.end, NOW)
        self.assertEqual(doc["matches"][0]["channels"], [])
        self.assertEqual(doc["channels"], {})

    def test_format_unchanged(self):
        doc = um.build(um.from_football_data(fd_answer(fd_match(1, "PL", "2026-10-29T18:00:00+00:00"))),
                       CHANNEL_MAP, self.start, self.end, NOW)
        self.assertEqual(set(doc), {"version", "updatedAt", "source", "channels", "matches"})
        self.assertEqual(set(doc["matches"][0]), um.FIELDS)
        self.assertEqual(doc["matches"][0]["kickoff"], "2026-10-29T18:00:00Z")
        self.assertEqual(doc["matches"][0]["channels"], ["bein1"])

    def test_tbd_teams_skipped(self):
        m = fd_match(1, "CL", "2026-10-29T18:00:00Z")
        m["homeTeam"] = {"name": None}
        self.assertEqual(um.from_football_data(fd_answer(m)), [])


class MergeTest(unittest.TestCase):
    def test_duplicate_ids_removed(self):
        ms = um.from_football_data(fd_answer(fd_match(1, "PL", "2026-10-29T18:00:00Z"), fd_match(1, "PL", "2026-10-29T18:00:00Z")))
        self.assertEqual(len(um.merge(ms)), 1)

    def test_same_fixture_from_second_source_dropped(self):
        fd = um.from_football_data(fd_answer(fd_match(1, "PL", "2026-10-29T18:00:00Z", home="Arsenal", away="Chelsea")))
        af = um.from_api_football({"response": [
            {"fixture": {"id": 9, "date": "2026-10-29T21:00:00+03:00", "status": {"short": "NS"}},
             "league": {"id": 39, "name": "Premier League", "country": "England"},
             "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea FC"}}},
            {"fixture": {"id": 10, "date": "2026-10-29T20:00:00+03:00", "status": {"short": "1H"}},
             "league": {"id": 542, "name": "Stars League", "country": "Iraq"},
             "teams": {"home": {"name": "Al Shorta"}, "away": {"name": "Al Zawraa"}}},
        ]})
        merged = um.merge(fd, af)
        self.assertEqual([m["id"] for m in merged], ["fd_1", "af_10"])
        self.assertEqual(merged[1]["status"], "IN_PLAY")
        self.assertEqual(merged[1]["competition"], "Stars League (Iraq)")
        self.assertEqual(merged[1]["kickoff"], "2026-10-29T17:00:00Z")


class FetchTest(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        calls, sleeps = [], []

        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "7"}, None)
            return io.BytesIO(b'{"matches": []}')

        self.assertEqual(um.http_json("https://x/m", {}, sleep=sleeps.append, opener=opener), {"matches": []})
        self.assertEqual(sleeps, [7])

    def test_no_retry_on_bad_key_and_no_key_in_message(self):
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, None)

        with self.assertRaises(um.SourceError) as cm:
            um.http_json("https://x/m", {"X-Auth-Token": "SECRET123"}, sleep=lambda s: None, opener=opener)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SECRET123", str(cm.exception))


class MainTest(unittest.TestCase):
    def run_main(self, out, fetch, env_key="SECRET123"):
        import os
        os.environ["FOOTBALL_DATA_API_KEY"] = env_key
        os.environ.pop("API_FOOTBALL_KEY", None)
        cmap = out.parent / "channel-map.json"
        cmap.write_text(json.dumps(CHANNEL_MAP), encoding="utf-8")
        err, so = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(so):
            code = um.main([], now=NOW, fetch=fetch, out=out, channel_map_path=cmap)
        return code, so.getvalue() + err.getvalue()

    def many(self, n, start_id=1):
        return fd_answer(*[fd_match(i, "PL", f"2026-10-30T{10 + i % 10:02d}:00:00Z") for i in range(start_id, start_id + n)])

    def test_writes_and_requests_baghdad_range(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            urls = []
            code, log = self.run_main(out, lambda url, h: urls.append(url) or self.many(3))
            self.assertEqual(code, 0)
            self.assertIn("dateFrom=2026-10-28&dateTo=2026-11-05", urls[0])
            self.assertEqual(len(json.loads(out.read_text(encoding="utf-8"))["matches"]), 3)
            self.assertNotIn("SECRET123", log)

    def test_failure_keeps_last_good_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            self.run_main(out, lambda url, h: self.many(12))
            before = out.read_text(encoding="utf-8")

            def boom(url, h):
                raise um.SourceError("HTTP 429")

            code, log = self.run_main(out, boom)
            self.assertEqual(code, 1)
            self.assertEqual(out.read_text(encoding="utf-8"), before)
            self.assertNotIn("SECRET123", log)

    def test_empty_answer_keeps_last_good_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            self.run_main(out, lambda url, h: self.many(12))
            before = out.read_text(encoding="utf-8")
            code, _ = self.run_main(out, lambda url, h: {"matches": []})
            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), before)

    def test_partial_answer_keeps_last_good_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            self.run_main(out, lambda url, h: self.many(20))
            before = out.read_text(encoding="utf-8")
            code, log = self.run_main(out, lambda url, h: self.many(5))
            self.assertEqual(code, 1)
            self.assertIn("partial", log)
            self.assertEqual(out.read_text(encoding="utf-8"), before)

    def test_missing_key_stops(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            code, _ = self.run_main(out, lambda url, h: self.many(3), env_key="")
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

    def test_garbage_answer_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "matches.json"
            code, _ = self.run_main(out, lambda url, h: {"message": "error"})
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
