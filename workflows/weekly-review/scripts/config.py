#!/usr/bin/env python3
"""
Tunable constants for the weekly-review workflow.

Kept as plain module constants rather than a YAML config file, per the 90-10
storage ladder ("start dumb, escalate only if needed"): the whole configuration
is five numbers. Adjust here to change the cadence, window, or synthesis bound.
"""

# Default look-back (in days) when there is no prior watermark, e.g. the very
# first review on a fresh clone.
WINDOW_DAYS = 7

# A review is considered "due" this many days after the last recorded one. Drives
# the AGENTS.md session-startup staleness gate (via `run.py --status`).
STALENESS_DAYS = 7

# How long an empty journal day stays on the backfill list. If a day is still
# blank this many days after its date, the review stops tracking it (it is
# treated as permanently unwritten rather than carried forever).
CARRY_FORWARD_DAYS = 28

# How many prior reviews to feed into synthesis. Bounded deliberately: feeding
# unbounded history invites the "older summaries override newer ones" context
# poisoning pitfall (OpenAI Build Hour, agent memory). The compounding value
# comes from reconciling against the most recent reviews, not all of them.
PRIOR_REVIEWS = 2

# Hard cap on the review span, so a long gap since the last review cannot explode
# the briefing packet. If more than this many days have passed, only the most
# recent MAX_WINDOW_DAYS are covered.
MAX_WINDOW_DAYS = 31
