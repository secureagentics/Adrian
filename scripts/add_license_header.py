#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
"""Prepend the Apache-2.0 SPDX header to source files that lack one.

Invoked by pre-commit with the staged filenames; scoping (which trees,
which extensions, vendored and generated files excluded) lives in
.pre-commit-config.yaml. Exits 1 when any file was modified so the
commit aborts and the fixed files can be reviewed and re-staged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SPDX = "SPDX-License-Identifier: Apache-2.0"
COPYRIGHT = "Copyright (c) 2026 SecureAgentics"

SLASH_SUFFIXES = {".go", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".proto"}
HASH_SUFFIXES = {".py", ".pyi", ".sh"}

ENCODING_DECL = re.compile(r"^[ \t\f]*#.*?coding[:=]")


def insertion_index(lines: list[str], path: Path) -> int:
    """Return the line index where the header goes.

    Skips a shebang and, for Python files, a PEP 263 encoding declaration
    (both must stay in the first two lines).
    """
    i = 0
    if lines and lines[0].startswith("#!"):
        i = 1
    if (
        path.suffix in {".py", ".pyi"}
        and len(lines) > i
        and ENCODING_DECL.match(lines[i])
    ):
        i += 1
    return i


def add_header(path: Path) -> bool:
    """Prepend the header to path if missing; return True when modified."""
    if path.suffix in SLASH_SUFFIXES:
        prefix = "//"
    elif path.suffix in HASH_SUFFIXES:
        prefix = "#"
    else:
        return False
    text = path.read_text(encoding="utf-8")
    if SPDX in "".join(text.splitlines(keepends=True)[:5]):
        return False
    lines = text.splitlines(keepends=True)
    i = insertion_index(lines, path)
    header = f"{prefix} {SPDX}\n{prefix} {COPYRIGHT}\n"
    rest = "".join(lines[i:])
    if rest and not rest.startswith(("\n", "\r")):
        header += "\n"
    path.write_text("".join(lines[:i]) + header + rest, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    """Process every file named in argv; return 1 when any was changed."""
    changed = [name for name in argv if add_header(Path(name))]
    for name in changed:
        print(f"added licence header: {name}")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
