#!/usr/bin/env python3
"""
Builds matches/matches.json (format version 1) for the IRAQ DIGITAL TV app.

Source: football-data.org API v4, /v4/matches with dateFrom/dateTo (free plan: 12 competitions,
10 calls/minute). Only upcoming fixtures are kept: no scores, minutes or statistics are written.
Broadcast channels are not provided by the source; they come from matches/channel-map.json,
which you maintain (competition code -> channel keys, and how to find each channel).

The file is only rewritten when the schedule actually changed, and an API failure or an empty
answer never replaces the last good file. Exit codes: 0 ok / unchanged, 1 error (file untouched).

Usage (GitHub Actions):  FOOTBALL_DATA_API_KEY=... python scripts/update_matches.py
Local test with a saved API answer:  python scripts/update_matches.py --input sample_api.json
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.football-data.org/v4/matches"
DAYS_AHEAD = 7  # keep well inside the API's maximum date range
# Upcoming states only; IN_PLAY / PAUSED / FINISHED carry live data and are excluded.
KEEP_STATUS = {"SCHEDULED", "TIMED", "POSTPONED", "SUSPENDED", "CANCELLED"}

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "matches" / "matches.json"
CHANNEL_MAP = ROOT / "matches" / "channel-map.json"


def fetch(token: str, date_from: str, date_to: str) -> dict:
    url = f"{API}?dateFrom={date_from}&dateTo={date_to}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": token, "User-Agent": "IRAQDigitalTV-matches/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def utc_z(value: str) -> str:
    """Normalise an ISO timestamp to UTC with the Z suffix (e.g. 2026-10-28T17:30:00Z)."""
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def team(t: dict) -> dict:
    return {
        "name": t.get("shortName") or t.get("name") or "?",
        "logo": t.get("crest") or t.get("crestUrl") or t.get("crestURI"),
    }


def build(api: dict, channel_map: dict) -> dict:
    by_competition = channel_map.get("competitions", {})
    catalogue = channel_map.get("channels", {})
    matches, used = [], set()
    for m in api.get("matches", []):
        status = (m.get("status") or "").upper()
        if status not in KEEP_STATUS or not m.get("utcDate"):
            continue
        home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        if not (home.get("name") or home.get("shortName")) or not (away.get("name") or away.get("shortName")):
            continue  # draws not made yet ("TBD") are skipped
        comp = m.get("competition") or {}
        keys = [k for k in by_competition.get(comp.get("code", ""), []) if k in catalogue]
        used.update(keys)
        matches.append({
            "id": f"fd_{m['id']}",
            "competition": comp.get("name", ""),
            "competitionCode": comp.get("code", ""),
            "home": team(home),
            "away": team(away),
            "kickoff": utc_z(m["utcDate"]),
            "status": status,
            "channels": keys,
        })
    matches.sort(key=lambda x: (x["kickoff"], x["id"]))
    return {
        "version": 1,
        "updatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "football-data.org",
        "channels": {k: catalogue[k] for k in sorted(used)},
        "matches": matches,
    }


def validate(doc: dict) -> None:
    assert doc["version"] == 1
    for m in doc["matches"]:
        assert m["id"] and m["home"]["name"] and m["away"]["name"]
        assert m["kickoff"].endswith("Z")
        assert set(m) == {"id", "competition", "competitionCode", "home", "away", "kickoff", "status", "channels"}
        utc_z(m["kickoff"])


def same_content(a: dict, b: dict) -> bool:
    strip = lambda d: {k: v for k, v in d.items() if k != "updatedAt"}
    return strip(a) == strip(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="read a saved API answer instead of calling the API")
    args = ap.parse_args()

    channel_map = json.loads(CHANNEL_MAP.read_text(encoding="utf-8")) if CHANNEL_MAP.exists() else {}
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        if args.input:
            api = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            token = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
            if not token:
                # Without a key the API still answers 200 with an empty list, which would look
                # like "no matches"; stop instead. The key itself is never printed.
                print("FOOTBALL_DATA_API_KEY secret is not set", file=sys.stderr)
                return 1
            api = fetch(token, today.isoformat(), (today + dt.timedelta(days=DAYS_AHEAD)).isoformat())
    except urllib.error.HTTPError as e:
        # 400 = invalid key, 403 = competition not in plan, 429 = rate limit. Body not echoed.
        print(f"API answered HTTP {e.code}, keeping the last good file", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"Source unavailable, keeping the last good file: {e}", file=sys.stderr)
        return 1

    if not isinstance(api, dict) or not isinstance(api.get("matches"), list):
        print("API answer has no 'matches' list, keeping the last good file", file=sys.stderr)
        return 1

    doc = build(api, channel_map)
    try:
        validate(doc)
    except (AssertionError, KeyError, ValueError) as e:
        print(f"Generated data is invalid, not publishing: {e!r}", file=sys.stderr)
        return 1
    if not doc["matches"]:
        print("Source returned no upcoming matches; keeping the last good file.")
        return 0

    if OUT.exists():
        try:
            if same_content(json.loads(OUT.read_text(encoding="utf-8")), doc):
                print("Schedule unchanged.")
                return 0
        except json.JSONDecodeError:
            pass  # a broken old file is replaced by the new valid one
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(doc['matches'])} matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
