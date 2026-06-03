# Web Research — config/

**Last modified:** 2026-05-29

## Purpose
Configuration files for the web research skill. Holds the RSS feed registry used by rss_source.py. API keys for keyed sources (Tavily, Brave, Guardian) are stored in `.env` at the project root, not here.

## Contents
- rss_feeds.yaml — `skills/web-research/config/rss_feeds.yaml` [[skills/web-research/config/CONTEXT]] — RSS feed URLs organised by topic category; consumed by sources/rss_source.py.

## Inputs
None. These are static config files read by the skill scripts.

## Outputs
None.

## Steps
N/A. This is a config container, not a workflow itself.

## Dependencies
- None.

## Known Issues
- RSS feed URLs in rss_feeds.yaml can go stale. Check periodically if a feed stops returning results and update or replace the URL.

## Revision History
- 2026-05-29 — Initial creation. rss_feeds.yaml added with six categories: ai-news, tech-general, uk-news, science, business, developer.
