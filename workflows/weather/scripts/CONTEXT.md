# Weather - Scripts

**Last modified:** 2026-08-04

## Purpose
Python scripts for the weather workflow. Handles geocoding via Nominatim, weather data retrieval via Open-Meteo, hourly condition grouping, and saved location management.

## Contents
- `run.py` - Main entry point. Supports `--location`, `--days`, `--save`, `--delete`, `--list`, and `--dry-run` flags. Run `python run.py --help` for full usage.

## Inputs
- CLI arguments (see `run.py --help` for full reference).
- `.env` at project root: `WEATHER_DEFAULT_LOCATION`, `USER_EMAIL`.
- `workflows/weather/locations.json` [[workflows/weather/CONTEXT]] - saved named locations (read and written by `--save`, `--delete`, `--list`).

## Outputs
- Formatted weather report printed to stdout.
- Updated `workflows/weather/locations.json` [[workflows/weather/CONTEXT]] when `--save` or `--delete` is used.

## Steps
N/A - see parent CONTEXT.md [[weather/CONTEXT]] for workflow steps.

## Dependencies
- Open-Meteo REST API (`api.open-meteo.com`) - no key required.
- Nominatim REST API (`nominatim.openstreetmap.org`) - no key required.
- Python standard library only - no external packages required.
- `workflows/weather/locations.json` [[workflows/weather/CONTEXT]] - read/written by location management commands.
- `.env` (project root) - default location and User-Agent email.

## Known Issues
- See parent CONTEXT.md [[weather/CONTEXT]] for full known issues list.

## Revision History
- 2026-06-03 — Initial creation.
- 2026-08-04 - Line endings pinned on both `locations.json` writes in `run.py`, which now pass `newline="\n"` explicitly. Part of the project-wide pass closing this defect class at all 48 write sites.
