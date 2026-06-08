#!/usr/bin/env python3
"""
_base.py — Abstract base class for Book Dragon session-search adapters.

Adapters are one-time import tools. They read from an AI tool's specific
cache format and yield normalised records for archive.py to write.

For ongoing session capture, hooks (configured in .claude/settings.json)
call archive.py directly — no adapter involvement after the initial import.
"""

from abc import ABC, abstractmethod
from typing import Iterator


class TranscriptAdapter(ABC):
    """
    Abstract base class for session transcript adapters.

    Subclasses must implement discover(), validate(), and parse().
    Each is a pure read operation — no writes happen inside adapters.
    archive.py handles all writes to data/archive/.
    """

    # Set SOURCE_LABEL in each subclass, e.g. 'claude-code', 'cowork'.
    SOURCE_LABEL: str = ''

    @abstractmethod
    def discover(self, project_root: str) -> list:
        """
        Return a list of file paths containing transcripts for this project.

        Args:
            project_root: Absolute path to the Book Dragon project root.

        Returns:
            List of Path objects pointing to transcript files.
            Returns an empty list if this source is not present on the machine.
        """

    @abstractmethod
    def validate(self, file_path: str) -> list:
        """
        Verify that expected fields are present in a transcript file.

        Called before parse() during a historical import run. Allows early
        detection of format changes (e.g. after an AI tool update breaks
        field names or nesting).

        Args:
            file_path: Path to the transcript file to validate.

        Returns:
            List of warning strings. An empty list means the file looks clean.
            A non-empty list means parse() may fail or produce incomplete output.
        """

    @abstractmethod
    def parse(self, file_path: str) -> Iterator[dict]:
        """
        Yield normalised message records from a transcript file.

        Skips tool calls, tool results, system messages, and any empty content.
        Yields only 'user' and 'assistant' message text.

        Args:
            file_path: Path to the transcript file to parse.

        Yields:
            dict with exactly these keys:
                role        (str)  'user' or 'assistant'
                content     (str)  plain text only; no structured blocks
                timestamp   (str)  ISO 8601 string
                session_id  (str)  unique session identifier
                source      (str)  matches SOURCE_LABEL
                hostname    (str)  machine hostname (socket.gethostname())
        """
