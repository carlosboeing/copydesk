#!/usr/bin/env python3
"""Atomic, reversible application of configuration and instructions."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, Union

MARKER_START = "<!-- copydesk:start -->"
MARKER_END = "<!-- copydesk:end -->"

_REGION = re.compile(
    re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?", re.DOTALL
)


class Target(NamedTuple):
    real: Path  # the file that is actually written
    aliases: list[Path]  # symlinks resolving to it, named in the review panel
    block: str  # the marked region to write


class Write(NamedTuple):
    path: Path
    content: str
    must_exist: bool = False


class Plan(NamedTuple):
    writes: list[Write]


class Result(NamedTuple):
    ok: bool
    failed: Optional[Path]
    message: str


def plan_targets(paths: Sequence[Union[str, Path]], block: str) -> list[Target]:
    """Coalesce paths that resolve to one real file.

    Global instruction files are often symlinks. Two links to one CLAUDE.md
    get one write, one backup and one review line naming both aliases.
    """
    by_real: dict[Path, Target] = {}
    for path in paths:
        p = Path(path)
        real = p.resolve()
        entry = by_real.setdefault(real, Target(real=real, aliases=[], block=block))
        if p != real:
            entry.aliases.append(p)
    return list(by_real.values())


def write_marked_block(path: Path, block: str) -> None:
    """Replace CopyDesk's region, or append one. Writes through a symlink.

    Opening the path given rather than its target means a link stays a link.
    Replacing the file would turn one shared instruction file into several.
    """
    region = f"{MARKER_START}\n{block}\n{MARKER_END}\n"
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    if _REGION.search(existing):
        updated = _REGION.sub(region, existing, count=1)
    else:
        separator = "" if not existing or existing.endswith("\n\n") else "\n"
        updated = existing + separator + region
    path.write_text(updated, encoding="utf-8")


def remove_marked_block(path: Path) -> None:
    """Delete the region and nothing else, leaving the file byte-identical
    to what it was before setup wrote into it."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return
    updated = _REGION.sub("", existing, count=1)
    if updated != existing:
        # The separator write_marked_block added goes with it.
        path.write_text(updated.rstrip("\n") + "\n" if updated.strip() else "", encoding="utf-8")


def execute(plan: Plan) -> Result:
    """Execute a plan atomically.

    Takes one backup per real file before writing. If any write fails,
    rolls back all modified files from original content and removes newly created files.
    """
    # 1. Pre-validation: check must_exist
    for write in plan.writes:
        if write.must_exist and not write.path.exists():
            return Result(ok=False, failed=write.path, message=f"{write.path} does not exist")

    # 2. Backups: one per real file that exists
    timestamp = time.strftime("%Y%m%d%H%M%S")
    backed_up_real_paths: set[Path] = set()
    created_backups: list[Path] = []
    original_contents: dict[Path, str] = {}
    created_files: list[Path] = []
    created_dirs: list[Path] = []

    try:
        for write in plan.writes:
            p = write.path
            real_p = p.resolve() if p.exists() or p.is_symlink() else p

            if p.exists() or (p.is_symlink() and real_p.exists()):
                if real_p not in backed_up_real_paths:
                    backed_up_real_paths.add(real_p)
                    content = real_p.read_text(encoding="utf-8")
                    original_contents[real_p] = content
                    backup_path = real_p.parent / f"{real_p.name}.copydesk-backup-{timestamp}"
                    backup_path.write_text(content, encoding="utf-8")
                    created_backups.append(backup_path)
            else:
                cur = p.parent
                new_dirs = []
                while cur != cur.parent and not cur.exists():
                    new_dirs.append(cur)
                    cur = cur.parent
                for d in reversed(new_dirs):
                    d.mkdir(parents=False, exist_ok=True)
                    created_dirs.append(d)

                created_files.append(p)

            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(write.content, encoding="utf-8")

        return Result(ok=True, failed=None, message="Applied successfully")

    except Exception as err:
        # Rollback
        for real_p, orig_text in original_contents.items():
            try:
                real_p.write_text(orig_text, encoding="utf-8")
            except OSError:
                pass

        for f in created_files:
            try:
                if f.exists() or f.is_symlink():
                    f.unlink()
            except OSError:
                pass

        for d in reversed(created_dirs):
            try:
                if d.exists() and not list(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

        for b in created_backups:
            try:
                if b.exists():
                    b.unlink()
            except OSError:
                pass

        failed_path = write.path if "write" in locals() else None
        return Result(ok=False, failed=failed_path, message=str(err))


def render_review(plan: Plan) -> str:
    """Render a human-readable summary of the plan."""
    lines = []
    for write in plan.writes:
        lines.append(f"  write {write.path}")
    return "\n".join(lines)
