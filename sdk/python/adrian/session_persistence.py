# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Persistent per-cwd session_id storage.

By default Adrian assigns a fresh ``session_id`` on every SDK init,
which means the dashboard sees a new session every process restart
even when it's the same deployment.  This module persists the
session_id at ``~/.adrian/projects/<cwd-key>/config.json`` so
subsequent runs from the same working directory keep the same
identifier.

The path key is the absolute cwd with ``/`` replaced by ``-``
(e.g. ``/home/user/proj`` → ``-home-user-proj``).

Resolution chain (in :func:`adrian.init`):

1. ``ADRIAN_SESSION_ID`` environment variable.
2. Explicit ``session_id=`` kwarg.
3. Persisted value at ``~/.adrian/projects/<cwd-key>/config.json``.
4. Fresh ``uuid4()`` written to that path for next time.

Containers / Docker without a volume-mounted ``$HOME`` get a fresh
session_id per container instance, same as the pre-persistence
behaviour.  Mount a volume at ``~/.adrian/`` to preserve continuity
across container restarts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

logger = logging.getLogger("adrian.session")

_CONFIG_FILENAME = "config.json"
_SESSION_KEY = "session_id"
_BASE_DIR_NAME = ".adrian"
_PROJECTS_DIR_NAME = "projects"


def _cwd_key(cwd: Path) -> str:
    r"""Encode an absolute path as a flat directory name.

    Replaces both POSIX (``/``) and Windows (``\``) path separators
    plus the drive-letter colon with ``-``, so ``/home/user/proj``
    becomes ``-home-user-proj`` and ``C:\Users\u\proj`` becomes
    ``-C-Users-u-proj``.
    """
    abs_str = str(cwd.resolve())
    return abs_str.replace("/", "-").replace("\\", "-").replace(":", "-")


def _config_dir(cwd: Path | None = None) -> Path:
    """Return the per-cwd config directory under ``~/.adrian/projects/``."""
    base = cwd if cwd is not None else Path.cwd()
    return Path.home() / _BASE_DIR_NAME / _PROJECTS_DIR_NAME / _cwd_key(base)


def _config_path(cwd: Path | None = None) -> Path:
    """Return the full path to the per-cwd config file."""
    return _config_dir(cwd) / _CONFIG_FILENAME


def _read_persisted(cwd: Path | None = None) -> str | None:
    """Return a previously-persisted session_id, or ``None``.

    Missing file, parse error, and read error all return ``None``
    (with a warning log on parse / read failures).  Callers fall
    back to generating a fresh value.
    """
    path = _config_path(cwd)

    if not path.exists():
        return None

    try:
        raw = path.read_text()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        logger.warning(
            "failed to read persisted session_id at %s: %s, "
            "falling back to a fresh UUID",
            path,
            exc,
        )
        return None

    if not isinstance(data, dict):
        return None

    sid = cast("dict[str, object]", data).get(_SESSION_KEY)

    if isinstance(sid, str) and sid:
        return sid

    return None


def _write_persisted(session_id: str, cwd: Path | None = None) -> None:
    """Write ``session_id`` to the per-cwd config file.

    Creates parent directories as needed.  Logs and continues on
    failure, persistence is best-effort, never load-bearing.
    """
    path = _config_path(cwd)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({_SESSION_KEY: session_id}, indent=2) + "\n"
        path.write_text(payload)
    except OSError as exc:
        logger.warning(
            "failed to persist session_id to %s: %s, "
            "subsequent runs will get a different session_id",
            path,
            exc,
        )


def resolve_session_id(cwd: Path | None = None) -> str:
    """Return the session_id for ``cwd``, generating one if absent.

    Reads from the per-cwd config file if present; otherwise
    generates a new UUID4 and writes it back so subsequent runs
    reuse the same identifier.

    Does NOT consult environment variables or kwargs, those are
    higher-priority and resolved in :func:`adrian.init` before this
    is called.

    Args:
        cwd: Project root.  Defaults to ``Path.cwd()``.

    Returns:
        A UUID4 string suitable for use as ``session_id``.
    """
    existing = _read_persisted(cwd)

    if existing is not None:
        return existing

    new_id = str(uuid4())
    _write_persisted(new_id, cwd)

    return new_id


# Alias used by some callers that prefer the env-var override-aware
# helper as one resolution chain, but most code paths go through
# ``adrian.init`` which composes env / kwarg / persistent itself.
def env_aware_resolve_session_id(
    explicit: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Full resolution chain: env var → ``explicit`` → persistent.

    Convenience wrapper that mirrors what :func:`adrian.init` does
    internally.  Useful for tests and tools that want the same
    layered defaulting without duplicating the precedence logic.
    """
    env = os.getenv("ADRIAN_SESSION_ID")

    if env:
        return env

    if explicit:
        return explicit

    return resolve_session_id(cwd)
