# Match schedule for IRAQ DIGITAL TV

These files go into the root of the GitHub repository `aliflex4k-source/IRAQ-DIGITAL-TV-ANDROID-APP`
(the same repository used for app updates). The app reads:

```
https://raw.githubusercontent.com/aliflex4k-source/IRAQ-DIGITAL-TV-ANDROID-APP/main/matches/matches.json
```

## Files

| Path | Purpose |
|---|---|
| `.github/workflows/update-matches.yml` | Runs every hour (minute 17) and on demand; commits only when the schedule changed |
| `scripts/update_matches.py` | Fetches upcoming fixtures from football-data.org and writes `matches/matches.json` |
| `matches/channel-map.json` | **You edit this**: which channels broadcast each competition |
| `matches/matches.json` | Generated; do not edit by hand |

## One-time setup

1. Create a free account at https://www.football-data.org/client/register and copy your API token.
   Check their terms for your use (the free plan: 12 competitions, 10 calls/minute).
2. In the repository: **Settings → Secrets and variables → Actions → New repository secret**,
   name `FOOTBALL_DATA_API_KEY`, value = the key. The key is never in the app, in any file, or in the run log.
3. **Settings → Actions → General → Workflow permissions**: allow *Read and write* if the workflow
   cannot push.
4. Upload the files above (keep the folder structure), then **Actions → Update match schedule →
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

- Only upcoming fixtures are kept (no scores, minutes or statistics). Times are UTC (`...Z`); the app
  shows them in the device's time zone.
- If the API fails, returns garbage, or returns no matches, the last good file stays published.
- GitHub may start scheduled runs late under load, and disables schedules in repositories with no
  activity for 60 days; re-enable it from the Actions tab if that happens.
- Coverage is limited to football-data.org's free competitions (no Iraqi or Saudi league).
