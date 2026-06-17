# Config

**Last modified:** 2026-06-14

## Purpose
Feed configuration for the Reddit collector. Defines which subreddits to collect, content filters, and series detection preferences.

## Contents
- `feeds.example.json` — example configuration with r/HFY pre-configured. Tracked in git as a template.
- `feeds.json` — active configuration used by the collector. Gitignored (may contain personal preferences). Created by copying `feeds.example.json` on first run.

## Inputs
None. This directory holds static configuration files.

## Outputs
None. Configuration is read by `scripts/run.py`.

## Steps
N/A. This is a configuration directory.

## Dependencies
None.

## Known Issues
- `feeds.json` is gitignored, so each machine needs its own copy. The collector will prompt the user to create one from `feeds.example.json` if it is missing.

## Revision History
- 2026-06-14 — Initial creation with feeds.example.json.
