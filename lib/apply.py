import json
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, Union

import instructions
import jsonc

MARKER_START = "<!-- copydesk:start -->"
MARKER_END = "<!-- copydesk:end -->"

_REGION = re.compile(
    re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?", re.DOTALL
)


class Target(NamedTuple):
    real: Path  # the file that is actually written
    aliases: list[Path] = []  # symlinks resolving to it, named in the review panel
    block: str = ""  # the marked region to write
    kind: str = "marked-block"  # "marked-block", "hook-keys", "created"


class Write(NamedTuple):
    path: Path
    content: str
    must_exist: bool = False


class Plan(NamedTuple):
    writes: list[Write]
    removes: "Sequence[Path]" = ()  # retired files, deleted once every write lands


class Result(NamedTuple):
    ok: bool
    failed: Optional[Path]
    message: str


def plan_targets(paths: Sequence[Union[str, Path]], block: str) -> list[Target]:
    """Coalesce paths that resolve to one real file.

    Global instruction files are often symlinks. Two links to one CLAUDE.md
    get one write and one review line naming both aliases.
    """
    by_real: dict[Path, Target] = {}
    for path in paths:
        p = Path(path)
        real = p.resolve()
        entry = by_real.setdefault(real, Target(real=real, aliases=[], block=block))
        if p != real:
            entry.aliases.append(p)
    return list(by_real.values())


def splice_marked_block(existing: str, block: str) -> str:
    """Return `existing` with CopyDesk's region replaced, or appended if absent.

    Pure by design. Every write goes through `execute` so the plan can roll
    back, which means the new text has to exist before anything touches disk.
    A variant that read the file itself would also have to decide what an
    unreadable file means, and that is the caller's decision: setup lets the
    read raise, so an unreadable file stops the run instead of being replaced
    by CopyDesk's region alone.
    """
    region = f"{MARKER_START}\n{block}\n{MARKER_END}\n"
    if _REGION.search(existing):
        return _REGION.sub(region, existing, count=1)
    separator = "" if not existing or existing.endswith("\n\n") else "\n"
    return existing + separator + region


def remove_marked_block(path: Path) -> None:
    """Delete the region and nothing else, leaving the file byte-identical
    to what it was before setup wrote into it."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return
    updated = _REGION.sub("", existing, count=1)
    if updated == existing:
        return
    if not updated.strip():
        # Nothing but our region was in it, so CopyDesk created it. Leaving a
        # zero-byte file behind is litter an uninstall should not produce.
        path.unlink(missing_ok=True)
        return
    # The separator splice_marked_block added goes with it.
    path.write_text(updated.rstrip("\n") + "\n", encoding="utf-8")


def _real_of(p: Path) -> Path:
    return p.resolve() if not p.is_symlink() else p


def execute(plan: Plan) -> Result:
    """Execute a plan atomically.

    Holds the original text of every file it touches in memory. If any write
    fails, rolls back all modified files from that text and removes newly
    created files. Planned removals run last and roll back the same way, so
    a failed plan leaves an install exactly as it was found — including the
    files it was about to delete. A removed path that was a symlink comes
    back as the same link, never as a regular file holding what it named.

    The originals stay in memory and never reach disk. A snapshot beside the
    file would outlive the command that needed it, and these are files such
    as settings.json that carry credentials: `write_text` creates a copy at
    the process umask, so the copy can be readable where the original was
    not.
    """
    # 1. Pre-validation: check must_exist
    for write in plan.writes:
        if write.must_exist and not write.path.exists():
            return Result(ok=False, failed=write.path, message=f"{write.path} does not exist")

    # 2. Originals: one read per real file that exists or is about to go
    original_contents: dict[Path, str] = {}
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    removed_originals: dict[Path, Optional[str]] = {}
    removed_symlinks: dict[Path, Path] = {}
    for p in plan.removes:
        real_p = _real_of(p)
        if real_p.is_symlink():
            try:
                removed_symlinks[real_p] = Path(os.readlink(real_p))
            except OSError:
                pass
            continue
        if real_p.is_file():
            try:
                removed_originals[real_p] = real_p.read_text(encoding="utf-8")
            except OSError:
                removed_originals[real_p] = None

    try:
        for write in plan.writes:
            p = write.path
            real_p = p.resolve() if p.exists() or p.is_symlink() else p

            if p.exists() or (p.is_symlink() and real_p.exists()):
                if real_p not in original_contents:
                    original_contents[real_p] = real_p.read_text(encoding="utf-8")
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

        # 3. Removals land only after every write succeeded, so a failure
        # above can never leave an install with neither the old file nor
        # the new one.
        for p in plan.removes:
            target = _real_of(p)
            if target.is_file():
                target.unlink()

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

        for link, target in removed_symlinks.items():
            try:
                if not link.is_symlink():
                    link.symlink_to(target)
            except OSError:
                pass

        for real_p, orig_text in removed_originals.items():
            if orig_text is None:
                continue
            try:
                real_p.parent.mkdir(parents=True, exist_ok=True)
                real_p.write_text(orig_text, encoding="utf-8")
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


def _write_atomic(path: Path, content: str) -> None:
    temp = path.parent / f"{path.name}.tmp.{os.getpid()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def _remove_copydesk_hooks(settings_path: Path) -> None:
    """Drop CopyDesk's hook entries and an outputStyle it owns.

    Every other key survives untouched. `outputStyle` is unset only when it
    names CopyDesk or one of the retired per-level styles; any other value
    is the user's and stays. Both edits share this rewrite so uninstall
    cannot leave a key pointing at a style file it has just unlinked.
    """
    if not settings_path.is_file():
        return
    try:
        raw = settings_path.read_text(encoding="utf-8")
        document = json.loads(jsonc.strip_comments(raw))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(document, dict):
        return
    changed = False
    hooks = document.get("hooks")
    if isinstance(hooks, dict):
        for event, entries in list(hooks.items()):
            kept = [
                entry for entry in entries
                if not any("copydesk" in str(h.get("command", "")) for h in entry.get("hooks", []))
            ]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
        if not hooks:
            document.pop("hooks", None)
        changed = True
    style_name = document.get("outputStyle")
    owned = (instructions.OUTPUT_STYLE_NAME, *instructions.LEGACY_OUTPUT_STYLE_NAMES)
    if isinstance(style_name, str) and style_name in owned:
        document.pop("outputStyle", None)
        changed = True
    if not changed:
        return
    if not document:
        # An empty object carries no configuration, so there is nothing to
        # preserve. Any other key keeps the file, which the tests assert.
        settings_path.unlink(missing_ok=True)
        return
    _write_atomic(settings_path, json.dumps(document, indent=2) + "\n")


def _prune_empty(start: Path, homes: "list[Path]") -> None:
    """Remove empty directories upward from `start`, stopping at a home.

    A harness home such as ~/.claude belongs to the harness, never to
    CopyDesk, so the walk stops below it. A directory holding anything at
    all is left alone, which is what makes this safe rather than tidy.
    """
    stops = {Path(h).resolve() for h in homes}
    current = start if start.is_dir() else start.parent
    while current.is_dir() and current.resolve() not in stops:
        try:
            next(current.iterdir())
            return                      # not empty: stop here
        except StopIteration:
            pass
        except OSError:
            return
        parent = current.parent
        try:
            current.rmdir()
        except OSError:
            return
        if parent == current:
            return
        current = parent


def remove_owned(targets: list[Target], homes: "list[Path]" = ()) -> Result:
    """Remove only what CopyDesk put there. Never restores earlier content.

    Setup keeps a file's original text for one command only, to roll back a
    failed apply. Restoring anything at uninstall time would discard every
    edit made since, so uninstall deletes CopyDesk's own region and stops.

    `homes` names the harness directories the prune must not climb past.
    """
    removed, failed = [], None
    for target in targets:
        try:
            if target.kind == "marked-block":
                remove_marked_block(target.real)
            elif target.kind == "hook-keys":
                _remove_copydesk_hooks(target.real)
            elif target.kind == "created":
                if target.real.is_dir():
                    shutil.rmtree(target.real, ignore_errors=True)
                else:
                    target.real.unlink(missing_ok=True)
            removed.append(target.real)
        except OSError as error:
            failed = target.real
            return Result(False, failed, f"{target.real}: {error.strerror}")
    for target in targets:
        _prune_empty(target.real, homes)
    return Result(True, None, f"removed {len(removed)} items")

