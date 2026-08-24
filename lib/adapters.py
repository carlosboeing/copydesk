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
    instruction_file: str  # "" names nothing: git takes a hook, not instructions


REGISTRY = {
    "claude-code": Adapter(
        name="claude-code", label="Claude Code",
        installs="adds output styles, two hooks and a CLAUDE.md block where channels need one",
        gate_verified=True,
        repeat_closer=True, home="~/.claude", instruction_file="CLAUDE.md",
    ),
    "codex": Adapter(
        name="codex", label="Codex",
        installs="adds a block to AGENTS.md", gate_verified=False,
        repeat_closer=False, home="~/.codex", instruction_file="AGENTS.md",
    ),
    "cursor": Adapter(
        name="cursor", label="Cursor",
        installs="adds a block to AGENTS.md", gate_verified=False,
        repeat_closer=False, home="~/.cursor", instruction_file="AGENTS.md",
    ),
    "kimi": Adapter(
        name="kimi", label="Kimi Code",
        installs="adds a block to AGENTS.md", gate_verified=False,
        repeat_closer=False, home="~/.agents", instruction_file="AGENTS.md",
    ),
    "opencode": Adapter(
        name="opencode", label="OpenCode",
        installs="installs a blocking write-gate plugin and adds a block to AGENTS.md",
        gate_verified=True,
        repeat_closer=False, home="~/.config/opencode", instruction_file="AGENTS.md",
    ),
    "antigravity": Adapter(
        name="antigravity", label="Antigravity CLI",
        installs="adds a block to AGENTS.md", gate_verified=False,
        repeat_closer=False, home="~/.agents", instruction_file="AGENTS.md",
    ),
    "grok": Adapter(
        name="grok", label="Grok Build",
        installs="installs a blocking PreToolUse gate and adds a block to shared instructions",
        gate_verified=True,
        repeat_closer=False, home="~/.grok", instruction_file="AGENTS.md",
    ),
    "git": Adapter(
        name="git", label="Git commit messages",
        installs="adds a commit-msg hook to this repository", gate_verified=False,
        repeat_closer=False, home=".", instruction_file="",
    ),
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
