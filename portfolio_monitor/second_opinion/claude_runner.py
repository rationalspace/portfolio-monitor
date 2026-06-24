"""Invokes headless Claude Code (``claude -p``) with zero tool permissions.

The prompt passed in is already fully self-contained (see ``prompt.py`` /
``context.py``) — this process does pure reasoning over it, with no
filesystem or shell access. Run as a subprocess (argv list, no shell) so the
prompt text can never be interpreted as shell syntax.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT_SECONDS = 60

# launchd daemons get a minimal PATH that often excludes ~/.local/bin, where
# the claude CLI typically lives — so PATH lookup alone isn't reliable when
# this runs unattended (vs. an interactive shell where `which claude` works).
_FALLBACK_CLAUDE_PATHS = [
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]


class ClaudeRunError(RuntimeError):
    pass


def _resolve_claude_binary() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _FALLBACK_CLAUDE_PATHS:
        if candidate.exists():
            return str(candidate)
    raise ClaudeRunError(
        "`claude` CLI not found on PATH or in known install locations "
        f"({', '.join(str(p) for p in _FALLBACK_CLAUDE_PATHS)})"
    )


def run_claude(prompt: str, *, timeout: int = CLAUDE_TIMEOUT_SECONDS) -> str:
    claude_bin = _resolve_claude_binary()
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--allowedTools", ""],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ClaudeRunError(f"`claude` CLI not found at resolved path {claude_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeRunError(f"claude -p timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise ClaudeRunError(f"claude -p exited {result.returncode}: {result.stderr.strip()}")

    return result.stdout.strip()
