# Match schedule for IRAQ DIGITAL TV

These files go into the root of the GitHub repository `aliflex4k-source/IRAQ-DIGITAL-TV-ANDROID-APP`
(the same repository used for app updates). The app reads:

```
https://raw.githubusercontent.com/aliflex4k-source/IRAQ-DIGITAL-TV-ANDROID-APP/main/matches/matches.json
```

## Files

| Path | Purpose |
|---|---|
| `.github/workflows/update-matches.yml` | Runs twice an hour (minutes 17 and 47) and on demand; tests the script, then commits only when the schedule changed |
| `scripts/update_matches.py` | Fetches the coming days' fixtures and writes `matches/matches.json` |
| `scripts/test_update_matches.py` | Tests for the script (`python -m unittest discover -s scripts`) |
| `matches/channel-map.json` | **You edit this**: which channels broadcast each competition |
| `matches/matches.json` | Generated; do not edit by hand |

## One-time setup

1. Create a free account at https://www.football-data.org/client/register and copy your API token.
   Check their terms for your use (the free plan: 12 competitions, 10 calls/minute).
2. In the repository: **Settings → Secrets and variables → Actions → New repository secret**,
   name `FOOTBALL_DATA_API_KEY`, value = the key. The key is never in the app, in any file, or in the run log.
3. **Settings → Actions → General → Workflow permissions**: allow *Read and write* if the workflow
   cannot push.
4. Optional, for leagues and cups football-data's plan lacks (Iraqi, Saudi, Egyptian leagues,
   domestic cups...): create a key at https://dashboard.api-football.com and add it as the secret
   `API_FOOTBALL_KEY`. Without it this source is simply skipped. The free plan allows 100 calls/day;
   each run uses 2 (today and tomorrow, `AF_DAYS`), so 48 runs/day stay inside it.
5. Upload the files above (keep the folder structure), then **Actions → Update match schedule →
   Run workflow** to test. `matches/matches.json` appears after the first successful run.

## Linking matches to channels

football-data.org does not say which TV channel shows a match, so `channel-map.json` does:

```json
"competitions": { "PL": ["bein_sports_1", "bein_sports_2"] },
"channels": { "bein_sports_1": { "name": "beIN SPORTS 1", "epgIds": ["<epg id>"], "patterns": ["\\bbe\\s*in\\s*sports?\\s*1\\b"] } }
```

- Competition codes: PL, PD, SA, BL1, FL1, CL, DED, PPL, ELC, BSA, EC, WC.
- `epgIds`: the channel's EPG id on your IPTV server (most reliable). `patterns`: channel-name regex as a fallback.
- The app finds every variant in the subscriber's own list (4K / FHD / HD / SD) and plays it through Live TV.
  Stream URLs are never stored in these files.

All competition lists start **empty** on purpose: broadcasting rights differ by region and season.

## Behaviour and limits

- Days follow Iraq time (Asia/Baghdad): the file holds every match from today 00:00 to the end of the
  8th day, **whatever its status** (scheduled, live, finished, postponed, cancelled). Today's finished and
  live matches stay listed with their state. No scores, minutes or statistics are written. Times in the
  file are UTC (`...Z`); the app shows them in Iraq time.
- A match is published even when no channel is mapped for its competition.
- Same match from both sources: kept once (football-data wins).
- If a source fails (with retries for rate limits and server errors), returns garbage, returns no
  matches, or suddenly loses more than 30% of the matches the last file had for the same days, the last
  good file stays published and the run log says why. The key is never printed. If a competition is
  removed from your plan on purpose, delete `matches/matches.json` once so the next run starts fresh.
- GitHub may start scheduled runs late under load, and disables schedules in repositories with no
  activity for 60 days; re-enable it from the Actions tab if that happens.
- Coverage without `API_FOOTBALL_KEY`: only the competitions in your football-data.org plan. The free
  plan has 12: Premier League, Championship, La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira
  Liga, Brasileirão, Champions League, European Championship, World Cup. No domestic cups (FA Cup, EFL
  Cup, Copa del Rey, Coppa Italia, DFB-Pokal), no Europa / Conference League, no Arab leagues. The run log
  lists the competitions actually returned. API-Football competition codes look like `AF307` (league id)
  and can be mapped to channels in `channel-map.json` like the others.
