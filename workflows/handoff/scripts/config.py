#!/usr/bin/env python3
"""
Tunable constants for the handoff workflow.

Kept as plain module constants rather than a config file, per the 90-10 storage
ladder ("start dumb, escalate only if needed"): the whole configuration is a
handful of values. Adjust here to change how much signal the packet gathers.
"""

# How many recent commits to list in the packet.
RECENT_COMMITS = 5

# How many trailing LOG.md entries to show per changed directory.
LOG_TAIL = 6

# How far back (in days, inclusive of today) to count "recent" session activity.
SESSION_LOOKBACK_DAYS = 1

# The rolling handoff document, at the project root. Overwritten each handoff and
# gitignored via the **/HANDOVER.md rule.
HANDOFF_FILENAME = "HANDOVER.md"
