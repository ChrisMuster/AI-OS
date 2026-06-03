# Image Prompt (Skill)

**Last modified:** 2026-05-29

## Purpose
Given a piece of written content (article, LinkedIn post, blog post, etc.), this skill analyses the content and recommends the most suitable type of accompanying image — real/personal photo, stock photo, or AI-generated — then delivers the appropriate output for the chosen path. For AI-generated images, it produces a fully structured prompt ready to paste into any image generator (ArtSpace.ai, Canva AI, Midjourney, Adobe Firefly, etc.).

## Contents
- SKILL.md — `skills/image-prompt/SKILL.md` [[skills/image-prompt/SKILL]] — Full skill specification: decision criteria, conversation flow, output formats, platform specs, and prompt construction guidelines.

## Inputs
- Content to analyse (file path or inline text)
- Platform target (linkedin, twitter, instagram-square, instagram-portrait, blog — default: linkedin)
- Style override (optional; default is photorealistic, but Biblio may override based on content)

## Outputs
One of three outputs depending on the recommendation and user choice:
- **Real/personal photo path** — Shot description, what to look for in own library, fallback stock search terms.
- **Stock photo path** — Search terms, what to look for, what to avoid, recommended site.
- **AI-generated path** — Full structured image prompt ready to paste into an image generator.

## Steps
N/A. This is a skill invoked by Biblio directly, not a standalone workflow with a script.

## Dependencies
- `CLAUDE.md` [[CLAUDE]] (root) — Governs UK English, CONTEXT.md schema, and shared skill conventions.
- Invoked by `workflows/web-research/` [[workflows/web-research/CONTEXT]] via the `--image-prompt` flag in `scripts/run.py`, or directly by Biblio on request.

## Known Issues
- None.

## Revision History
- 2026-05-29 — Initial creation. Decision matrix, three output paths, platform specs, and prompt construction guidelines defined.
