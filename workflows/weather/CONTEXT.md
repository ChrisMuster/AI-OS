# Weather

**Last modified:** 2026-06-20

## Purpose
Fetches current weather conditions and forecasts for any location worldwide, using Open-Meteo (weather data) and Nominatim/OpenStreetMap (geocoding). No API keys required.

## Contents
- scripts/ — `workflows/weather/scripts/` [[workflows/weather/scripts/CONTEXT]] — Python scripts for the workflow. Entry point: `workflows/weather/scripts/run.py`.
- `locations.json` — Saved named locations (gitignored; created automatically on first use).

## Inputs
- A location: postcode, place name, or saved location name. Falls back to `WEATHER_DEFAULT_LOCATION` in `.env` if no `--location` flag is given.
- `--days N`: number of forecast days (1 = today only; 2–16 for multi-day).
- `.env` at project root: `WEATHER_DEFAULT_LOCATION` (default query) and `USER_EMAIL` (used in Nominatim User-Agent).

## Outputs
- Formatted weather report to stdout: current temperature, high/low, sky conditions, wind speed and direction.
- For today (`--days 1`): hourly condition breakdown if conditions vary through the day.
- For multi-day: compact per-day summary, expanding automatically when conditions shift within a day.
- Temperatures shown in °C with °F in parentheses; wind in mph.

## Steps
1. Resolve the location: check `locations.json` for a matching saved name, then geocode via Nominatim.
2. Fetch current conditions, daily summary, and hourly data from Open-Meteo in a single API call.
3. Group consecutive identical hourly weather codes into condition blocks.
4. Format and print the report.
5. (For `--save` / `--delete` / `--list` commands): update `locations.json` accordingly and exit.
6. Append `LOG.md` with a completion or failure entry.

## Dependencies
- Open-Meteo API (`api.open-meteo.com`) — free, no key, global coverage; provides current conditions, daily high/low, and hourly forecasts.
- Nominatim API (`nominatim.openstreetmap.org`) — free, no key, global coverage; converts postcodes and place names to coordinates.
- `.env` [[.env]] (project root) — `WEATHER_DEFAULT_LOCATION` for the default query; `USER_EMAIL` for the Nominatim User-Agent header (Nominatim usage policy requires a descriptive User-Agent).
- Python standard library only (`urllib`, `json`, `math`, `pathlib`, `argparse`) — no pip install required.

## Known Issues
- `locations.json` is gitignored (personal data). It is created automatically on first run. On a fresh clone it will not exist until the first `--save` or any weather query is made.
- Open-Meteo hourly condition grouping compares WMO weather codes exactly. Small code variations (e.g. code 1 vs 2 for "mainly clear" vs "partly cloudy") may produce more condition blocks than expected on some days.
- Nominatim has a usage-policy rate limit of 1 request per second. For normal conversational use this is never a concern, but looped scripting should add a delay.
- The Nominatim `display_name` field returns the full address chain; the script trims to the first three comma-separated parts (city, region, country). Unusual locations may trim oddly.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-06-20 — Fixed the Contents entry for the scripts subdirectory to use the project-root-relative path `workflows/weather/scripts/` and the correct `[[workflows/weather/scripts/CONTEXT]]` link (previously a bare `scripts/` token and a malformed `[[weather/scripts/CONTEXT]]` link that the knowledge-graph indexer could not resolve, flagging the directory as uncontained).
