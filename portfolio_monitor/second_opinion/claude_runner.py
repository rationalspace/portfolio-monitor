"""Invokes headless Claude Code (``claude -p``) with zero tool permissions.

The prompt passed in is already fully self-contained (see ``prompt.py`` /
``context.py``) — this process does pure reasoning over it, with no
filesystem or shell access. Run as a subprocess (argv list, no shell) so the
prompt text can never be interpreted as shell syntax.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT_SECONDS = 60


class ClaudeRunError(RuntimeError):
    pass


def run_claude(prompt: str, *, timeout: int = CLAUDE_TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", ""],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ClaudeRunError("`claude` CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeRunError(f"claude -p timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise ClaudeRunError(f"claude -p exited {result.returncode}: {result.stderr.strip()}")

    return result.stdout.strip()
