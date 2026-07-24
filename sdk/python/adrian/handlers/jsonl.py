# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""JSONL file handler for paired events.

Writes each ``PairedEvent`` as a single JSON line to a file. This is
the default handler when no handlers are explicitly passed to
``adrian.init()``, preserving backward compatibility with the original
JSONL-only mode.

Thread-safe via ``threading.Lock`` because LangChain can dispatch
callbacks from a thread pool when using the synchronous API.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path

from adrian.format.types import PairedEvent

logger = logging.getLogger("adrian.handlers.jsonl")


class JSONLHandler:
    """Writes paired events to an append-only JSONL file.

    Attributes:
        path: The output file path.
    """

    def __init__(self, path: Path | str) -> None:
        """Create a JSONL handler.

        Creates parent directories if needed and opens the file for
        writing. Overwrites any existing file.

        Args:
            path: Path to the JSONL output file.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "w", encoding="utf-8")  # noqa: SIM115
        self._lock = threading.Lock()

    async def on_paired_event(self, event: PairedEvent) -> None:
        """Write a paired event as a single JSON line.

        Serialises the dataclass to a dict, writes as JSON, and
        flushes immediately to avoid data loss on crash.

        Args:
            event: The paired event to write.
        """
        record = asdict(event)
        line = json.dumps(record, default=str)

        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    async def close(self) -> None:
        """Flush and close the output file."""
        with self._lock:
            self._file.flush()
            self._file.close()
