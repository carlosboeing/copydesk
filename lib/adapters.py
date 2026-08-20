#!/usr/bin/env python3
"""What each harness receives, and whether its gate is verified.

A mount existing is not an adapter working. Each harness claims gate support
only after a live transcript shows a block. Until then the wizard says only
what the adapter installs, and upgrades its own line the day that changes.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple


class Adapter(NamedTuple):
    name: str
    label: str
    installs: str
    gate_verified: bool
    repeat_closer: bool
    home: str


REGISTRY = {
    "claude-code": Adapter(
        "claude-code", "Claude Code",
        "adds output styles and two hooks", True, True, "~/.claude",
    ),
    "codex": Adapter("codex", "Codex", "adds a block to AGENTS.md", False, False, "~/.codex"),
    "cursor": Adapter("cursor", "Cursor", "adds a block to AGENTS.md", False, False, "~/.cursor"),
    "kimi": Adapter("kimi", "Kimi Code", "adds a block to AGENTS.md", False, False, "~/.agents"),
    "opencode": Adapter("opencode", "OpenCode", "adds a block to AGENTS.md", False, False, "~/.config/opencode"),
    "antigravity": Adapter("antigravity", "Antigravity CLI", "adds a block to AGENTS.md", False, False, "~/.agents"),
    "grok": Adapter("grok", "Grok Build", "adds a block to shared instructions", False, False, "~/.grok"),
    "git": Adapter("git", "Git commit messages", "adds a commit-msg hook to this repository", False, False, "."),
}


# Kimi Code and Antigravity both live under ~/.agents, so a directory test
# cannot tell them apart: installing either would mark both available. The
# executable is the signal that distinguishes them.
EXECUTABLES = {
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
    "kimi": "kimi",
    "antigravity": "agy",
    "grok": "grok",
    "opencode": "opencode",
}

# Homes shared by more than one adapter. For these, the executable decides.
SHARED_HOMES = {"~/.agents"}


def detect(name: str, home: Path) -> bool:
    """Whether that harness is installed.

    The executable on PATH is the primary signal. A home directory counts
    only when it belongs to one adapter, because a shared one proves nothing
    about which of them is installed.
    """
    adapter = REGISTRY.get(name)
    if adapter is None or adapter.home == ".":
        return False
    if shutil.which(EXECUTABLES.get(name, name)):
        return True
    if adapter.home in SHARED_HOMES:
        return False
    return (home / adapter.home.replace("~/", "")).is_dir()
