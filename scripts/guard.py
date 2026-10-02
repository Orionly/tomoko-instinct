#!/usr/bin/env python3
"""
Tomoko Instinct - Write Guard

safe_write(path, content) refuses to write content that is below the
minimum safe size for an engine file. This protects the project from
accidental 0-byte / truncated overwrites of mission-critical files.

Guarded files and their minimum sizes:
    Main.py                 -> 500  bytes
    backend/api.py          -> 1000 bytes
    core/mt5_bridge.py      -> 500  bytes
    ui/web_dashboard.html   -> 1000 bytes
    config.py               -> 100  bytes

Usage:
    from scripts.guard import safe_write
    safe_write("Main.py", new_source)

If the content is too small, ValueError is raised and NO write happens.
"""

import os

# Path -> minimum acceptable content length in characters (bytes-ish).
MIN_SIZES = {
    "Main.py": 500,
    "backend/api.py": 1000,
    "core/mt5_bridge.py": 500,
    "ui/web_dashboard.html": 1000,
    "config.py": 100,
}


def _normalise(path):
    """Normalise separators so keys match on Windows and POSIX."""
    return path.replace("\\", "/").lstrip("./")


def safe_write(path, content):
    """
    Write content to path ONLY if it meets the minimum size for that file.

    Raises:
        ValueError: if the path is a known engine file and len(content) < minimum,
                    or if the path is unknown (no minimum configured).
    """
    normalised = _normalise(os.fspath(path))

    if normalised not in MIN_SIZES:
        raise ValueError(
            f"BLOCKED: {path!r} is not in the guarded file list "
            f"{sorted(MIN_SIZES)}. Refusing to write unknown files."
        )

    minimum = MIN_SIZES[normalised]
    if len(content) < minimum:
        raise ValueError(
            f"BLOCKED: write to {path!r} has {len(content)} chars, "
            f"below the minimum {minimum} chars. File was NOT modified."
        )

    # Ensure the parent directory exists.
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)

    return len(content)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("usage: python scripts/guard.py <path> <size-guard-smoke-content>", file=sys.stderr)
        sys.exit(2)

    try:
        written = safe_write(sys.argv[1], sys.argv[2])
        print(f"OK: wrote {written} chars to {sys.argv[1]}")
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        sys.exit(1)