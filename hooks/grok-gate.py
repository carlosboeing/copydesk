#!/usr/bin/env python3
"""Grok Build TUI PreToolUse adapter.

Reads Grok's camelCase hook envelope from stdin, translates it to the
envelope lib/linter.py already parses, and runs the same linter the Claude
Code gate runs. No rule lives here: lint, retry counts, severity handling
and fail-open behaviour stay in linter.py.

Translation is envelope-only:

- ``toolName`` ``write`` / ``search_replace`` map to ``Write`` / ``Edit``.
  Grok 1.0.5 sends snake_case fields inside ``toolInput`` (file_path,
  content, old_string, new_string), so those pass through untouched.
- ``search_replace`` carries no ``replace_all``; linter.py refuses to guess,
  so a missing one is injected as false rather than letting the edit fail
  open past the gate.
- ``sessionId`` gains a ``grok-`` prefix, which gives Grok its own retry
  state files instead of sharing three-strike counters with Claude Code.
- A relative ``file_path`` is resolved against the payload's
  ``workspaceRoot``. Grok's own tool declaration says the model may send
  "a relative path in the workspace or an absolute path", and Grok's hook
  contract names no working directory for the runner, so an unresolved
  relative path reaches linter.py as a path that may not exist. The read
  then fails, the gate exits 0, and the write lands unjudged.

Decisions follow Grok's documented hook contract: exit 2 with stderr as the
deny reason, or stdout JSON ``{"decision": "deny", "reason": ...}``. The
linter's findings are printed one per line on stderr, so the full text rides
a deny decision rather than only its first line. Any internal error exits 0:
the gate fails open and Grok records the failure in its scrollback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_NAMES = {
    "write": "Write",
    "search_replace": "Edit",
}


def _linter_path() -> Path | None:
    """The installed copy beside this script, else the source bundle."""
    override = os.environ.get("COPYDESK_LINTER")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    here = Path(__file__).resolve()
    candidates = (
        here.parent / "linter.py",          # installed ~/.grok/hooks/copydesk/
        here.parents[1] / "lib" / "linter.py",  # source bundle
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _workspace_root(payload: dict) -> Path | None:
    """Where a relative file_path is anchored.

    The payload carries both keys in a grok 1.0.5 capture. The environment
    variables are the documented fallback: Grok injects them for hooks and
    guarantees no working directory of its own.
    """
    for key in ("workspaceRoot", "cwd"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return Path(value)
    for name in ("GROK_WORKSPACE_ROOT", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return None


def translate(payload: object) -> dict | None:
    """Map Grok's PreToolUse payload to the CopyDesk envelope, or None."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("toolName")
    tool_input = payload.get("toolInput")
    session_id = payload.get("sessionId")
    mapped = TOOL_NAMES.get(tool) if isinstance(tool, str) else None
    if mapped is None or not isinstance(tool_input, dict):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    translated_input = dict(tool_input)
    file_path = translated_input.get("file_path")
    if isinstance(file_path, str) and file_path and not os.path.isabs(file_path):
        root = _workspace_root(payload)
        if root is not None:
            translated_input["file_path"] = str(root / file_path)
        # With no root to anchor it, the path passes through as it arrived.
        # That is what happened before this resolution existed, so the gate
        # is never worse off than it was.
    if mapped == "Edit" and "replace_all" not in translated_input:
        translated_input["replace_all"] = False
    return {
        "tool_name": mapped,
        "tool_input": translated_input,
        "session_id": f"grok-{session_id}",
    }


def main(argv: list[str] | None = None) -> int:
    del argv  # the gate takes no arguments; stdin is the interface
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return 0
        translated = translate(payload)
        if translated is None:
            return 0
        linter = _linter_path()
        if linter is None:
            return 0
        result = subprocess.run(
            [sys.executable, str(linter), "--hook"],
            input=json.dumps(translated),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 2:
            reason = result.stderr.strip() or "CopyDesk blocked this Markdown write."
            print(json.dumps({"decision": "deny", "reason": reason}))
            return 0
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
