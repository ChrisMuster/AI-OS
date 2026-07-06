#!/usr/bin/env python3
"""
Tunable constants for the memory-diff workflow.

Kept as plain module constants rather than a config file, per the 90-10 storage
ladder ("start dumb, escalate only if needed"): the whole configuration is a
couple of values. Adjust here to change what the startup diff surfaces.
"""

# The append-only memory log this workflow diffs, relative to the project root.
MEMORY_LOG = "memory/LOG.md"

# Soft cap on how many changed entries to list in a single startup summary, so a
# very stale watermark cannot dump a huge block into the greeting. The full count
# is always reported; only the printed list is trimmed to the most recent N.
MAX_ENTRIES = 15
