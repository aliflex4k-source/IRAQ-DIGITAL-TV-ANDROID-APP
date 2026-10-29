#!/usr/bin/env python3
"""
Builds matches/matches.json (format version 1) for the IRAQ DIGITAL TV app.

Sources
  1. football-data.org API v4, /v4/matches?dateFrom&dateTo (secret FOOTBALL_DATA_API_KEY).
     One call returns every match of every competition the key's plan includes (free plan:
     12 competitions, 10 calls/minute; no domestic cups, no Arab leagues). The answer is not
     paginated. The competitions actually returned are printed in the log.
  2. Optional: API-Football (api-sports.io), secret API_FOOTBALL_KEY. Adds the leagues and cups
     football-data does not cover (e.g. Iraqi, Saudi, Egyptian leagues, domestic cups). One call
     per day for the first AF_DAYS days (free plan: 100 calls/day). Skipped when the secret is unset.

Days are counted in Iraq time (Asia/Baghdad, UTC+3, no daylight saving): the file holds every
match from today 00:00 to the end of the 8th day, whatever its status (scheduled, live, finished,
postponed...), so today's finished and live matches stay listed with their state. No scores,
minutes or statistics are written. Broadcast channels come from matches/channel-map.json; a match
without a mapped channel is still published.

The last good file is never replaced by an empty, broken or clearly partial answer.
Exit codes: 0 ok / unchanged, 1 error (file untouched).

Usage (GitHub Actions):  FOOTBALL_DATA_API_KEY=... python scripts/update_matches.py
Local test with saved answers:  python scripts/update_matches.py --input fd.json [--input-af af.json]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FD_API = "https://api.football-data.org/v4/matches"
AF_API = "https://v3.football.api-sports.io/fixtures"
DAYS = 8  # today + 7; with the extra UTC day the request spans 9 days (API maximum: 10)
AF_DAYS = int(os.environ.get("AF_DAYS", "2") or 2)

# Iraq has used UTC+3 all year since 2008; a fixed offset avoids needing tz data on the runner.
BAGHDAD = dt.timezone(dt.timedelta(hours=3), "Asia/Baghdad")

FIELDS = {"id", "competition", "competitionCode", "home", "away", "kickoff", "status", "channels"}
STATUSES = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "FINISHED", "POSTPONED", "SUSPENDED", "CANCELLED", "AWARDED"}

# A new answer that loses more than this share of the matches the last good file had inside the
# same window (at least MIN_COMPARE of them) is treated as partial and not published.
MAX_MISSING_SHARE = 0.3
MIN_COMPARE = 10

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "matches" / "matches.json"
CHANNEL_MAP = ROOT / "matches" / "channel-map.json"


class SourceError(Exception):
    """Source unavailable or answered with an error; the message never contains a key."""


# ---------------------------------------------------------------- time window

def window(now: dt.datetime) -> tuple:
    """(start, end) in UTC: today 00:00 Baghdad to the end of the last day, end exclusive."""
    start_local = now.astimezone(BAGHDAD).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + dt.timedelta(days=DAYS)
    return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


def request_dates(start: dt.datetime, end: dt.datetime) -> tuple:
    """football-data filters by UTC calendar date (both ends inclusive)."""
    return start.date().isoformat(), (end - dt.timedelta(seconds=1)).date().isoformat()


def baghdad_days(start: dt.datetime, count: int) -> list:
    first = start.astimezone(BAGHDAD).date()
    return [(first + dt.timedelta(days=i)).isoformat() for i in range(count)]


def parse_time(value: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def utc_z(value: str) -> str:
    """Normalise an ISO timestamp to UTC with the Z suffix (e.g. 2026-10-28T17:30:00Z)."""
    return parse_time(value).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- HTTP

def http_json(url: str, headers: dict, attempts: int = 3, sleep=time.sleep, opener=urllib.request.urlopen) -> dict:
    """GET JSON with retries on rate limits (429) and server/network errors."""
    last = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={**headers, "User-Agent": "IRAQDigitalTV-matches/2"})
            with opener(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # 400 = invalid key, 403 = not in plan: retrying will not help. Body not echoed.
            last = f"HTTP {e.code}"
            if e.code != 429 and e.code < 500:
                break
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = min(int(retry_after), 65) if retry_after and retry_after.isdigit() else 15 * attempt
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = type(e).__name__
            wait = 10 * attempt
        if attempt < attempts:
            sleep(wait)
    raise SourceError(last)


# ---------------------------------------------------------------- sources

def team(t: dict) -> dict:
    return {
        "name": t.get("shortName") or t.get("name") or "?",
        "logo": t.get("crest") or t.get("crestUrl") or t.get("crestURI") or t.get("logo"),
    }


def from_football_data(api: dict) -> list:
    out = []
    for m in api.get("matches", []):
        home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        if not m.get("utcDate") or not (home.get("name") or home.get("shortName")) or not (away.get("name") or away.get("shortName")):
            continue  # draws not made yet ("TBD")
        comp = m.get("competition") or {}
        status = (m.get("status") or "SCHEDULED").upper()
        out.append({
            "id": f"fd_{m['id']}",
            "competition": comp.get("name", ""),
            "competitionCode": comp.get("code", ""),
            "home": team(home),
            "away": team(away),
            "kickoff": utc_z(m["utcDate"]),
            "status": status if status in STATUSES else "SCHEDULED",
        })
    return out


AF_STATUS = {
    "TBD": "SCHEDULED", "NS": "TIMED",
    "1H": "IN_PLAY", "2H": "IN_PLAY", "ET": "IN_PLAY", "P": "IN_PLAY", "LIVE": "IN_PLAY",
    "HT": "PAUSED", "BT": "PAUSED", "INT": "PAUSED",
    "FT": "FINISHED", "AET": "FINISHED", "PEN": "FINISHED",
    "PST": "POSTPONED", "CANC": "CANCELLED", "ABD": "SUSPENDED", "SUSP": "SUSPENDED",
    "AWD": "AWARDED", "WO": "AWARDED",
}


def from_api_football(api: dict) -> list:
    out = []
    for r in api.get("response", []):
        fx, league, teams = r.get("fixture") or {}, r.get("league") or {}, r.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if not fx.get("id") or not fx.get("date") or not home.get("name") or not away.get("name"):
            continue
        name = league.get("name", "")
        if league.get("country") and league["country"] not in ("World", name):
            name = f"{name} ({league['country']})"
        out.append({
            "id": f"af_{fx['id']}",
            "competition": name,
            "competitionCode": f"AF{league.get('id', '')}",
            "home": {"name": home["name"], "logo": home.get("logo")},
            "away": {"name": away["name"], "logo": away.get("logo")},
            "kickoff": utc_z(fx["date"]),
            "status": AF_STATUS.get(((fx.get("status") or {}).get("short") or "NS").upper(), "SCHEDULED"),
        })
    return out


# ---------------------------------------------------------------- merge / build

_NOISE = re.compile(r"\b(fc|cf|afc|sc|ac|club|de|the)\b|[^a-z0-9]+")


def norm(name: str) -> str:
    return _NOISE.sub("", name.lower())


def merge(*sources: list) -> list:
    """Earlier sources win; the same fixture from a later source (same teams, kickoff within
    90 minutes) and repeated ids are dropped."""
    out, ids, earlier = [], set(), []
    for src in sources:
        added = []
        for m in src:
            if m["id"] in ids:
                continue
            k = parse_time(m["kickoff"])
            pair = (norm(m["home"]["name"]), norm(m["away"]["name"]))
            # Only fixtures from earlier sources count as the same match; within one source
            # different ids are different matches.
            if any(p == pair and abs((k - t).total_seconds()) <= 5400 for p, t in earlier):
                continue
            ids.add(m["id"])
            added.append((pair, k))
            out.append(m)
        earlier += added
    return out


def build(matches: list, channel_map: dict, start: dt.datetime, end: dt.datetime, now: dt.datetime) -> dict:
    by_competition = channel_map.get("competitions", {})
    catalogue = channel_map.get("channels", {})
    kept, used = [], set()
    for m in matches:
        if not start <= parse_time(m["kickoff"]) < end:
            continue
        keys = [k for k in by_competition.get(m["competitionCode"], []) if k in catalogue]
        used.update(keys)
        kept.append({**m, "channels": keys})
    kept.sort(key=lambda x: (x["kickoff"], x["id"]))
    return {
        "version": 1,
        "updatedAt": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "football-data.org" + (" + api-football" if any(m["id"].startswith("af_") for m in kept) else ""),
        "channels": {k: catalogue[k] for k in sorted(used)},
        "matches": kept,
    }


def validate(doc: dict) -> None:
    assert doc["version"] == 1
    ids = [m["id"] for m in doc["matches"]]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for m in doc["matches"]:
        assert set(m) == FIELDS, f"fields of {m.get('id')}"
        assert m["id"] and m["home"]["name"] and m["away"]["name"]
        assert m["kickoff"].endswith("Z")
        assert m["status"] in STATUSES
        assert all(k in doc["channels"] for k in m["channels"])
        utc_z(m["kickoff"])


def looks_partial(old: dict, new: dict, start: dt.datetime, end: dt.datetime) -> str:
    """Reason why [new] seems to have lost data compared with the last good file, or ''."""
    prev = {m["id"] for m in old.get("matches", []) if start <= parse_time(m["kickoff"]) < end}
    if len(prev) < MIN_COMPARE:
        return ""
    missing = prev - {m["id"] for m in new["matches"]}
    if len(missing) > MAX_MISSING_SHARE * len(prev):
        return f"{len(missing)} of {len(prev)} known matches in the window are missing"
    return ""


def same_content(a: dict, b: dict) -> bool:
    strip = lambda d: {k: v for k, v in d.items() if k != "updatedAt"}
    return strip(a) == strip(b)


# ---------------------------------------------------------------- main

def collect(args, start: dt.datetime, end: dt.datetime, fetch=http_json) -> list:
    if args.input:
        fd = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        token = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
        if not token:
            # Without a key the API still answers 200 with an empty list, which would look
            # like "no matches"; stop instead. The key itself is never printed.
            raise SourceError("FOOTBALL_DATA_API_KEY secret is not set")
        date_from, date_to = request_dates(start, end)
        fd = fetch(f"{FD_API}?dateFrom={date_from}&dateTo={date_to}", {"X-Auth-Token": token})
    if not isinstance(fd, dict) or not isinstance(fd.get("matches"), list):
        raise SourceError("football-data answer has no 'matches' list")
    rs = fd.get("resultSet") or {}
    comps = sorted({(m.get("competition") or {}).get("code", "?") for m in fd["matches"]})
    print(f"football-data: {len(fd['matches'])} matches, competitions: {rs.get('competitions') or ','.join(comps) or '-'}")
    sources = [from_football_data(fd)]

    af_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if args.input_af:
        sources.append(from_api_football(json.loads(Path(args.input_af).read_text(encoding="utf-8"))))
    elif af_key:
        extra = []
        for day in baghdad_days(start, AF_DAYS):
            try:
                af = fetch(f"{AF_API}?date={day}&timezone=Asia/Baghdad", {"x-apisports-key": af_key})
            except SourceError as e:
                print(f"api-football {day}: {e}; using football-data only for that day", file=sys.stderr)
                continue
            if af.get("errors"):
                # e.g. daily limit reached; the error text names the plan, not the key.
                print(f"api-football {day}: {af['errors']}", file=sys.stderr)
                continue
            extra += from_api_football(af)
        print(f"api-football: {len(extra)} fixtures")
        sources.append(extra)
    return merge(*sources)


def main(argv=None, now=None, fetch=http_json, out=OUT, channel_map_path=CHANNEL_MAP) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="read a saved football-data answer instead of calling the API")
    ap.add_argument("--input-af", help="read a saved API-Football answer")
    args = ap.parse_args(argv)

    now = now or dt.datetime.now(dt.timezone.utc)
    start, end = window(now)
    channel_map = json.loads(channel_map_path.read_text(encoding="utf-8")) if channel_map_path.exists() else {}
    try:
        matches = collect(args, start, end, fetch)
    except SourceError as e:
        print(f"Source unavailable ({e}), keeping the last good file", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as e:
        print(f"Unreadable source data ({type(e).__name__}), keeping the last good file", file=sys.stderr)
        return 1

    doc = build(matches, channel_map, start, end, now)
    try:
        validate(doc)
    except (AssertionError, KeyError, ValueError) as e:
        print(f"Generated data is invalid, not publishing: {e!r}", file=sys.stderr)
        return 1
    if not doc["matches"]:
        print("Source returned no matches for the coming days; keeping the last good file.")
        return 0

    old = None
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # a broken old file is replaced by the new valid one
    if old:
        reason = looks_partial(old, doc, start, end)
        if reason:
            print(f"Answer looks partial ({reason}); keeping the last good file", file=sys.stderr)
            return 1
        if same_content(old, doc):
            print("Schedule unchanged.")
            return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)  # atomic: a crash never leaves half a file
    per_day = {}
    for m in doc["matches"]:
        d = parse_time(m["kickoff"]).astimezone(BAGHDAD).date().isoformat()
        per_day[d] = per_day.get(d, 0) + 1
    print(f"Wrote {len(doc['matches'])} matches (Baghdad days: {per_day}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
