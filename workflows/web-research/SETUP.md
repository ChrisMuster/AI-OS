# Web Research — Setup Guide

**Last updated:** 2026-06-06

## What is web research?

The web research workflow queries multiple sources in parallel and produces a structured research package containing findings, sources, and corroboration data. Biblio then uses that package to write a report.

Other workflows in this project use web research as a dependency when they need up-to-date information. If web research is not configured, those workflows will run with reduced source coverage and may produce less complete results.

## How it works without API keys

Web research runs out of the box using free, public sources with no accounts or configuration needed:

| Source | What it covers |
|---|---|
| Wikipedia | Encyclopaedic background |
| HackerNews | Technology discussion and links |
| Reddit | Community perspectives |
| arXiv | Academic preprints |
| Semantic Scholar | Academic paper search |
| Stack Exchange | Technical Q&A |
| Dev.to | Developer articles |
| RSS feeds | Configurable news sources |
| Direct scraper | Any URL you provide |

These sources are sufficient for many research tasks. API keys add coverage for general web content and current news, which some topics require.

## What API keys add

Three additional sources become available with API keys. Each has a free or low-cost entry tier that is generous for personal use:

| Source | What it adds | Entry tier |
|---|---|---|
| Tavily | General web search — broad coverage of current content | 1,000 searches/month, no card required |
| Brave Search | Independent web index — alternative coverage for cross-checking | $5 credit every month (~1,000 queries); card required for overages beyond the monthly credit |
| The Guardian | Full-text access to 2.7 million articles since 1999 | 500 calls/day, no card required |

## Prerequisites

**Python 3.9 or later** must be installed. See `REQUIREMENTS.md` if not yet set up.

Install the web-research Python packages once before using the workflow. Run this from the project root:

    python workflows/biblio-tools/scripts/setup.py

You only need to run this once.

## Obtaining API keys

### Tavily

Tavily provides AI-optimised web search results. Free tier: 1,000 searches/month, no credit card required.

1. Go to [app.tavily.com](https://app.tavily.com) and create a free account (email, Google, or GitHub).
2. Your API key is shown on the main dashboard after sign-in.

*If this link is out of date, ask your AI assistant: "How do I sign up for a Tavily API key?"*

### Brave Search

Brave Search provides an independent web index. All plans include $5 in credit that renews every month (~1,000 queries). A card is required and overages beyond the monthly credit are billed. Brave also requires attribution — this project's `README.md` includes the required credit, so no further action is needed on your part.

1. Go to [api-dashboard.search.brave.com](https://api-dashboard.search.brave.com) and create an account.
2. Add a payment method to activate your monthly credit.
3. Your API key is available in the dashboard after setup.

*If this link is out of date, ask your AI assistant: "How do I sign up for a Brave Search API key?"*

### The Guardian

The Guardian Open Platform gives free access to Guardian and Observer articles. Free developer tier: 500 calls/day, no credit card required.

1. Go to [open-platform.theguardian.com](https://open-platform.theguardian.com) and register for a developer key.
2. Fill in your email address and a brief description of your project.
3. Your key is emailed to you within a few minutes.

*If this link is out of date, ask your AI assistant: "How do I get a Guardian Open Platform API key?"*

## Configuration

1. If `.env` does not already exist at the project root, copy `.env.example` to `.env`.
2. Open `.env` and fill in the keys you have:

       TAVILY_API_KEY=your-key-here
       BRAVE_API_KEY=your-key-here
       GUARDIAN_API_KEY=your-key-here

Leave any entry blank if you do not have that key — the workflow automatically skips sources with no key configured. You do not need all three.

## Subscription sites — paywalled content

Some sites require a paid subscription to read articles in full. The web research pipeline cannot bypass paywalls, but it can detect when a URL from a site you subscribe to could not be read, and flag it for you to retrieve manually.

### How it works

When the scraper encounters a URL from a subscribed domain and cannot extract content (paywall response), it records the URL rather than silently discarding it. At the end of a research run, any such URLs are printed under a **PAYWALLED CONTENT** section. You can then visit those URLs yourself, copy the article text, and paste it into the chat — Biblio will include it as a source in the report.

This only applies to the direct scraper (when you pass `--urls` to the workflow). URLs appearing in search results from Tavily or Brave are already in the research package as snippets; flagging is most useful when you pass a specific article URL you want fully read.

### Configuration

In your `.env`, add `SUBSCRIBED_DOMAINS` as a comma-separated list of the domains you have subscriptions to:

    SUBSCRIBED_DOMAINS=medium.com,ground.news,name.substack.com

Rules:
- List only domains you actually subscribe to — flagging is per-subscription, not site-wide.
- For Substack newsletters, add each one by its specific subdomain (e.g. `name.substack.com`). Do not add `substack.com` as a blanket entry — you may have subscriptions to some newsletters but not others.
- Ask Biblio to update this list whenever you add a new subscription.

The `--check` flag shows which domains are currently configured.

---

## Verification

**Step 1 — static check** (no network calls, no credits used):

    python workflows/web-research/scripts/run.py --check

This confirms Python is the right version, all required packages are installed, `.env` exists, and reports which API keys are present or absent. Fix any items marked `[FAIL]` or `[MISSING]` before continuing.

**Step 2 — live test** (makes real source requests):

    python workflows/web-research/scripts/run.py --topic "test" --sources 3

A successful run prints a source summary and saves a research package to `workflows/web-research/outputs/`. The output shows which sources responded and which were skipped.

If a source with a key configured is being skipped, check that the key is correctly entered in `.env` with no extra spaces, and that the account is active on the provider's dashboard.
