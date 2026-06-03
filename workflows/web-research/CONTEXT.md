# Web Research (Workflow)

**Last modified:** 2026-05-29

## Purpose
User-facing CLI workflow for researching a topic and saving a research package ready for Biblio to turn into a report. Thin wrapper around the shared `skills/web-research/` [[skills/web-research/CONTEXT]] engine — all source logic lives in the skill; this workflow provides the command-line interface, output management, and report brief.

## Contents
- scripts/run.py — `workflows/web-research/scripts/run.py` [[workflows/web-research/scripts/CONTEXT]] — CLI entry point. Parses flags, calls the skill, saves the research package to outputs/, and prints the report brief for Biblio.
- config/sources.yaml — `workflows/web-research/config/sources.yaml` [[workflows/web-research/config/CONTEXT]] — Default source priority order and per-source settings for this workflow.
- outputs/ — `workflows/web-research/outputs/` [[workflows/web-research/outputs/CONTEXT]] — Where research packages (JSON) and reports (Markdown) are saved.

## Inputs
All inputs are passed as CLI flags. Only `--topic` is required; everything else has sensible defaults.

**Research flags:**
- `--topic` (required) — Research question or subject.
- `--sources` (default 8) — Max number of sources.
- `--include` — Comma-separated source names to query.
- `--exclude` — Comma-separated source names to skip.
- `--rss-category` — RSS category from skills/web-research/config/rss_feeds.yaml.
- `--urls` — Comma-separated URLs for the direct scraper.

**Output flags:**
- `--type` (default: article) — Content type: social-post, blog-post, article, briefing, newsletter, summary.
- `--words` (default: 1000) — Target word count.
- `--tone` (default: informational) — formal, conversational, journalistic, academic, casual.
- `--audience` (default: general) — general, technical, executive, academic.
- `--style` (default: informational) — informational, analytical, comparative, summary.

**Quality flags:**
- `--citations` (default: inline) — inline, endnotes, none.
- `--confidence-markers` — Include confidence markers on claims.
- `--readability-check` — Run readability scoring post-generation.
- `--virality` — Optimise for engagement and shareability.

**Image prompt flags:**
- `--image-prompt` — After the report brief, include an image prompt brief for Biblio to apply the image-prompt skill.
- `--platform` (default: linkedin) — Target platform for the image: linkedin, twitter, instagram-square, instagram-portrait, blog.

**Other:**
- `--output` — Custom output filename.
- `--dry-run` — Preview without making changes.

## Outputs
- `outputs/research-[slug]-[date].json` — Research package with sources, tiers, and corroboration data.
- `outputs/report-[slug]-[date].md` — Final report written by Biblio from the research package (produced in the Biblio step, not the script step).

## Steps
1. Run the script: `python workflows/web-research/scripts/run.py --topic "..." [flags]`
2. Script queries all active sources and saves the research package to `outputs/`.
3. Script prints a report brief summarising what was found and what Biblio needs to write.
4. Ask Biblio to read the research package and write the report to `outputs/`.
5. Append LOG.md with a completion or failure entry.

## Dependencies
- `skills/web-research/` [[skills/web-research/CONTEXT]] — Core research engine; all source adapters and compile logic.
- `skills/web-research/scripts/requirements.txt` [[skills/web-research/scripts/CONTEXT]] — Python dependencies (install once).
- `skills/web-research/config/rss_feeds.yaml` [[skills/web-research/config/CONTEXT]] — RSS feed list used by the rss source.
- `skills/image-prompt/` [[skills/image-prompt/CONTEXT]] — Image prompt skill; invoked when `--image-prompt` flag is passed.
- Python 3.8+ on the host machine.

## Known Issues
- Some sources (Semantic Scholar, Stack Exchange) have rate limits on unauthenticated requests. Exclude them if you hit 429 errors.
- The report step requires Biblio — there is no automated report generation in Step 1. This will be optional in a later step via the Claude API.
- outputs/ is not version-controlled; reports are local-only unless manually committed.

## Revision History
- 2026-05-29 — Initial creation. Step 1: free sources, CLI wrapper, research package output. Report step is Biblio-driven.
- 2026-05-29 — Added --image-prompt and --platform flags to run.py. Image prompt brief appended to Biblio brief when flag is present. Dependency on skills/image-prompt/ added.
