#!/usr/bin/env python3
"""Manage the commit-msg hook across repositories: add, remove, list.

The registry at <state>/hooks.json is a hint, never the truth. The hook file
on disk is the truth: every read opens the file each entry names and looks for
MARKER_PHRASE, and an entry that no longer matches is pruned rather than
reported. A registry that will not parse reads as empty with a warning, which
is the same fail-open rule the gate follows.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, TextIO

import linter
import prompt

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

HOOK_NAME = "commit-msg"

# The line that says a commit-msg hook is CopyDesk's. Setup replaces a hook
# carrying it and uninstall removes one; anything else belongs to the
# repository and is left alone.
HOOK_MARKER = "# CopyDesk commits gate"

# The words without the comment marker. Registry verification and removal
# search for these rather than HOOK_MARKER, because the chained block's guard
# lines are "# >>> CopyDesk commits gate >>>" and "# <<< CopyDesk commits gate
# <<<": the words are there, the "# " prefix is not. One search for the words
# finds an installation of either kind.
MARKER_PHRASE = "CopyDesk commits gate"

# The block appended to a foreign hook on request. Both guard lines carry
# MARKER_PHRASE, because the registry verifies an entry by searching the hook
# for those words and a chained entry lives inside a script CopyDesk did not
# write. A block marked any other way would read as missing, and every chained
# entry would be pruned the first time anything looked.
#
# The block is appended last, so its own last command sets the hook's exit
# status. It captures the foreign script's status first — the line above it
# is a comment, and comments do not run — and exits with it, so a foreign
# refusal survives chaining and a passing check exits 0.
#
# The host script may run under `set -e`, which the block inherits. So the
# check's status is captured through `|| status=$?` rather than `; status=$?`:
# errexit ends the script at a failing simple command, and an internal error
# would refuse the commit instead of failing open. A missing CopyDesk is
# guarded before the call for the same reason, as git-hooks/commit-msg does.
# The test is POSIX, because the host script may be #!/bin/sh.
BLOCK_START = "# >>> CopyDesk commits gate >>>"
BLOCK_END = "# <<< CopyDesk commits gate <<<"

CHAINED_BLOCK = """# >>> CopyDesk commits gate >>>
foreign=$?
COPYDESK="${COPYDESK_BIN:-copydesk}"
if command -v "$COPYDESK" >/dev/null 2>&1 || [ -x "$COPYDESK" ]; then
  status=0
  "$COPYDESK" check --commit-msg "$1" || status=$?
  if [ "$status" -eq 1 ]; then exit 1; fi
  if [ "$status" -gt 1 ]; then echo "copydesk: exit $status; commit allowed" >&2; fi
fi
exit "$foreign"
# <<< CopyDesk commits gate <<<
"""

# Both marker lines tolerate leading whitespace: _chain_instructions prints
# the block indented, and a verbatim paste must still strip cleanly.
_BLOCK_REGION = re.compile(
    r"^[ \t]*" + re.escape(BLOCK_START) + r"\n.*?"
    r"^[ \t]*" + re.escape(BLOCK_END) + r"\n?",
    re.DOTALL | re.MULTILINE,
)

# A message that breaks no rule, so a refusal during the probe comes from the
# foreign hook rather than from CopyDesk.
_PROBE_MESSAGE = "fix: expire reset tokens after first use\n"


def _clean_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _rev_parse(cwd: Path, *args: str) -> Optional[Path]:
    """One path answer from git, or None outside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", *args],
            cwd=str(cwd), capture_output=True, text=True, env=_clean_git_env(),
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    p = Path(out)
    return p.resolve() if p.is_absolute() else (cwd / p).resolve()


def hooks_directory(cwd: Path) -> Optional[Path]:
    """Git's own answer, which honours worktrees and core.hooksPath."""
    return _rev_parse(cwd, "--git-path", "hooks")


def common_git_dir(cwd: Path) -> Optional[Path]:
    """The value registry entries are keyed on.

    Two worktrees of one repository share it, so they collapse into one
    entry. Two repositories pointing core.hooksPath at one directory do not,
    which is what keeps a shared hook from reading as one repository.
    """
    return _rev_parse(cwd, "--git-common-dir")


def repo_root(cwd: Path) -> Optional[Path]:
    """The working-tree top level, for display. None for a bare repository."""
    return _rev_parse(cwd, "--show-toplevel")


# --- The registry ---------------------------------------------------------


def registry_path() -> Path:
    # The name comes from linter, which sweeps stale *.json out of this
    # directory and skips the registry by that name. Spelling it here as well
    # would let the two drift, and the drift deletes the registry.
    return linter._state_directory() / linter.HOOK_REGISTRY_NAME


def _load_entries() -> list[dict]:
    """The recorded entries. Broken JSON is empty with a warning, not an error."""
    path = registry_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        print(f"warning: {path} could not be read; treating it as empty", file=sys.stderr)
        return []
    entries = document.get("repositories") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        print(f"warning: {path} holds no repository list; treating it as empty", file=sys.stderr)
        return []
    return [e for e in entries if isinstance(e, dict)]


def _write_entries(entries: list[dict]) -> None:
    """Atomic: a temporary file renamed over the target. Callers hold the lock."""
    path = registry_path()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as temporary:
        json.dump({"version": 1, "repositories": entries}, temporary, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _locked_update(mutator: Callable[[list[dict]], object]) -> object:
    """Read, change and write the registry under the state directory's lock."""
    state_dir = registry_path().parent
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".hooks.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            entries = _load_entries()
            result = mutator(entries)
            _write_entries(entries)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _verify(entry: dict) -> bool:
    """Whether an entry still matches the hook on disk."""
    git_dir = entry.get("git_dir")
    if not git_dir or not Path(git_dir).exists():
        return False  # the repository is gone
    hook_file = Path(entry.get("hooks_dir", "")) / entry.get("hook", HOOK_NAME)
    try:
        content = hook_file.read_text(encoding="utf-8")
    except OSError:
        return False  # the hook was removed by hand
    has_marker = MARKER_PHRASE in content
    if entry.get("state") == "skipped":
        return not has_marker
    return has_marker


def verified_entries() -> list[dict]:
    """Every entry that still matches its hook. Stale entries are pruned."""
    entries = _load_entries()
    kept = [e for e in entries if _verify(e)]
    if len(kept) != len(entries):
        def prune(current: list[dict]) -> None:
            current[:] = [e for e in current if _verify(e)]

        try:
            _locked_update(prune)
        except OSError:
            pass  # a stale entry is reported as gone either way
    return kept


def record_repository(cwd: Path, state: str) -> None:
    """Upsert the repository `cwd` sits in. Never fails the caller."""
    try:
        git_dir = common_git_dir(cwd)
        hooks_dir = hooks_directory(cwd)
        if git_dir is None or hooks_dir is None:
            return
        root = repo_root(cwd) or cwd.resolve()
        entry = {
            "path": str(root),
            "git_dir": str(git_dir),
            "hooks_dir": str(hooks_dir),
            "hook": HOOK_NAME,
            "state": state,
            "added": datetime.date.today().isoformat(),
        }

        def upsert(entries: list[dict]) -> None:
            for index, existing in enumerate(entries):
                if existing.get("git_dir") == entry["git_dir"] and existing.get("hook") == HOOK_NAME:
                    entries[index] = {**existing, **entry, "added": existing.get("added", entry["added"])}
                    return
            entries.append(entry)

        _locked_update(upsert)
    except OSError as error:
        print(f"warning: could not record the repository ({error})", file=sys.stderr)


def _drop(entries: list[dict], git_dirs: set[str]) -> None:
    entries[:] = [e for e in entries if e.get("git_dir") not in git_dirs]


def forget_entries(git_dirs: set[str]) -> None:
    if not git_dirs:
        return
    try:
        _locked_update(lambda entries: _drop(entries, git_dirs))
    except OSError as error:
        print(f"warning: could not update {registry_path()} ({error})", file=sys.stderr)


def other_entries(cwd: Path) -> list[dict]:
    """Verified entries for every repository except the one `cwd` sits in."""
    git_dir = common_git_dir(cwd)
    return [e for e in verified_entries() if git_dir is None or e.get("git_dir") != str(git_dir)]


# --- Chaining -------------------------------------------------------------


def strip_block(content: str) -> Optional[str]:
    """The script with the appended region removed, or None when absent.

    A script that ended in a newline before the append comes back
    byte-identical. One that did not gained a newline when the block went in,
    and which side of the region that newline belongs to is not recorded, so
    it stays. Scripts without a trailing newline are rare enough that the
    marker comment sits on its own line either way.
    """
    match = _BLOCK_REGION.search(content)
    if match is None:
        return None
    return content[: match.start()] + content[match.end():]


def strip_file(hook_file: Path) -> bool:
    """Remove the appended region from a chained hook. False when absent."""
    content = hook_file.read_text(encoding="utf-8")
    stripped = strip_block(content)
    if stripped is None:
        return False
    hook_file.write_text(stripped, encoding="utf-8")
    return True


def _shell_shebang(content: str) -> bool:
    """Whether the script is sh, bash or zsh. Anything else must not get shell
    lines appended to it."""
    first = content.split("\n", 1)[0]
    if not first.startswith("#!"):
        return False
    tokens = first[2:].split()
    if not tokens:
        return False
    interpreter = tokens[0]
    if os.path.basename(interpreter) == "env":
        interpreter = next((t for t in tokens[1:] if not t.startswith("-")), "")
    return os.path.basename(interpreter) in ("sh", "bash", "zsh")


def _probe_reached(hook_file: Path, cwd: Path) -> bool:
    """Run the hook with a stub CopyDesk. True when the appended block ran.

    Proving reachability, not refusal: a commitlint-style hook rejects any
    message on its own, so exit 1 says nothing about whether CopyDesk's lines
    ran. The stub records having been called, and a message that breaks no
    rule reaches the end of a script that lets it.
    """
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        called = scratch / "called"
        stub = scratch / "copydesk-stub"
        stub.write_text('#!/bin/sh\n: >> "$COPYDESK_PROBE"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
        message = scratch / "message"
        message.write_text(_PROBE_MESSAGE, encoding="utf-8")
        env = _clean_git_env()
        env["COPYDESK_BIN"] = str(stub)
        env["COPYDESK_PROBE"] = str(called)
        try:
            subprocess.run(
                [str(hook_file), str(message)],
                cwd=str(cwd), env=env, capture_output=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return called.exists()


def _chain_instructions(target: Path) -> str:
    lines = "\n".join(f"             {line}" for line in CHAINED_BLOCK.rstrip("\n").split("\n"))
    return (
        f"skipped  {target} already exists\n"
        f"         To chain CopyDesk into it, add these lines at the end:\n"
        f"{lines}"
    )


def _canonical_hook() -> str:
    return (BUNDLE_ROOT / "git-hooks" / HOOK_NAME).read_text(encoding="utf-8")


# --- Removal --------------------------------------------------------------


def remove_entry(
    entry: dict,
    entries: list[dict],
    *,
    yes: bool,
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    """Remove or strip the hook one entry names. True when it may be forgotten.

    A hook CopyDesk wrote is deleted. A foreign hook with an appended block
    keeps everything but that region. A foreign hook with no block is left
    alone and reported.
    """
    hook_file = Path(entry.get("hooks_dir", "")) / entry.get("hook", HOOK_NAME)
    shared = [
        e for e in entries
        if e is not entry
        and e.get("git_dir") != entry.get("git_dir")
        and e.get("hooks_dir") == entry.get("hooks_dir")
    ]
    if shared and not yes:
        names = ", ".join(e.get("path", "?") for e in shared)
        try:
            confirmed = prompt.confirm(
                f"{hook_file} is shared with {names}, which would lose it too. Remove it?",
                default=False, stdin=stdin, stdout=stdout,
            )
        except prompt.Cancelled:
            confirmed = False
        if not confirmed:
            stdout.write(f"kept  {hook_file} (shared with {names})\n")
            return False
    try:
        content = hook_file.read_text(encoding="utf-8")
    except OSError:
        stdout.write(f"gone  {hook_file} (nothing on disk)\n")
        return True
    if MARKER_PHRASE not in content:
        stdout.write(f"left  {hook_file} (not written by CopyDesk)\n")
        return True
    if BLOCK_START in content:
        stripped = strip_block(content)
        if stripped is None:
            # The start marker is there but the block does not match. Deleting
            # would take a script CopyDesk did not write with it.
            stdout.write(f"left  {hook_file} (the marker is present but the block could not be located)\n")
            return False
        hook_file.write_text(stripped, encoding="utf-8")
        stdout.write(f"stripped  {hook_file} (the rest of the script is untouched)\n")
        return True
    hook_file.unlink()
    stdout.write(f"removed  {hook_file}\n")
    return True


def remove_entries(
    entries: list[dict],
    *,
    yes: bool,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    """Remove every entry given, verifying each first. Shared hooks default to no."""
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    forgotten: set[str] = set()
    for entry in entries:
        try:
            if remove_entry(entry, entries, yes=yes, stdin=in_stream, stdout=out_stream):
                forgotten.add(entry.get("git_dir", ""))
        except OSError as error:
            out_stream.write(f"error  {entry.get('path', '?')}: {error}\n")
    forgotten.discard("")
    if forgotten:
        forget_entries(forgotten)
    return 0


# --- The commands ---------------------------------------------------------


def _entry_for(cwd: Path, entries: list[dict]) -> Optional[dict]:
    git_dir = common_git_dir(cwd)
    hooks_dir = hooks_directory(cwd)
    if git_dir is None or hooks_dir is None:
        return None
    for entry in entries:
        if entry.get("git_dir") == str(git_dir):
            return entry
    # Unrecorded, but the disk may still hold a hook CopyDesk wrote. The
    # registry is a hint; the file is the truth.
    return {
        "path": str(repo_root(cwd) or cwd.resolve()),
        "git_dir": str(git_dir),
        "hooks_dir": str(hooks_dir),
        "hook": HOOK_NAME,
        "state": "unrecorded",
        "added": "",
    }


def _cmd_add_one(cwd: Path, *, yes: bool, stdin: TextIO, stdout: TextIO) -> int:
    git_dir = common_git_dir(cwd)
    hooks_dir = hooks_directory(cwd)
    if git_dir is None or hooks_dir is None:
        stdout.write(f"skipped  {cwd} is not a git repository\n")
        return 1
    hook_file = hooks_dir / HOOK_NAME

    entries = verified_entries()
    shared = [
        e for e in entries
        if e.get("git_dir") != str(git_dir) and e.get("hooks_dir") == str(hooks_dir)
    ]
    if shared:
        names = ", ".join(e.get("path", "?") for e in shared)
        stdout.write(f"note  {hooks_dir} is shared with {names} - one hook serves all of them\n")

    if not hook_file.is_file():
        try:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_file.write_text(_canonical_hook(), encoding="utf-8")
            hook_file.chmod(0o755)
        except OSError as error:
            stdout.write(f"error  cannot write {hook_file}: {error}\n")
            return 1
        record_repository(cwd, "installed")
        stdout.write(f"installed  {hook_file}\n")
        return 0

    try:
        content = hook_file.read_text(encoding="utf-8")
    except OSError as error:
        stdout.write(f"error  cannot read {hook_file}: {error}\n")
        return 1

    if MARKER_PHRASE in content:
        if BLOCK_START in content:
            record_repository(cwd, "chained")
            stdout.write(f"already chained  {hook_file}\n")
            return 0
        canonical = _canonical_hook()
        if content != canonical:
            hook_file.write_text(canonical, encoding="utf-8")
            hook_file.chmod(0o755)
            record_repository(cwd, "installed")
            stdout.write(f"updated  {hook_file}\n")
            return 0
        record_repository(cwd, "installed")
        stdout.write(f"already installed  {hook_file}\n")
        return 0

    # A hook someone else wrote: chain on request, never overwrite.
    chain = yes
    if not yes:
        try:
            chain = prompt.confirm(
                f"{hook_file} already exists. Append CopyDesk's block to it?",
                default=True, stdin=stdin, stdout=stdout,
            )
        except prompt.Cancelled:
            chain = False
    if not chain or not _shell_shebang(content):
        if chain:
            stdout.write(f"{hook_file} is not an sh, bash or zsh script.\n")
        record_repository(cwd, "skipped")
        stdout.write(_chain_instructions(hook_file) + "\n")
        return 0

    appended = content if content.endswith("\n") else content + "\n"
    hook_file.write_text(appended + CHAINED_BLOCK, encoding="utf-8")
    hook_file.chmod(hook_file.stat().st_mode | 0o111)
    root = repo_root(cwd) or cwd.resolve()
    if _probe_reached(hook_file, root):
        record_repository(cwd, "chained")
        stdout.write(f"chained  {hook_file} (verified: the block runs)\n")
        return 0
    record_repository(cwd, "unreachable")
    stdout.write(
        f"appended  {hook_file}, but a test run never reached the block -\n"
        f"          the script exits above it, so the commits gate is not live.\n"
    )
    return 1


def _cmd_add(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    if args.scan is not None and args.paths:
        print("error: --scan takes a directory instead of paths", file=sys.stderr)
        return 64
    if args.all and args.scan is None:
        print("error: --all needs --scan", file=sys.stderr)
        return 64
    if args.scan is not None:
        return _cmd_add_scan(Path(args.scan), args.all, args.yes, stdin, stdout)
    paths = [Path(p) for p in args.paths] or [Path.cwd()]
    result = 0
    for path in paths:
        result |= _cmd_add_one(path.resolve(), yes=args.yes, stdin=stdin, stdout=stdout)
    return result


def _cmd_add_scan(scan_dir: Path, take_all: bool, yes: bool, stdin: TextIO, stdout: TextIO) -> int:
    """Offer the repositories one level under `scan_dir`. Nothing is a default."""
    if not scan_dir.is_dir():
        print(f"error: {scan_dir} is not a directory", file=sys.stderr)
        return 64
    candidates = [
        child for child in sorted(scan_dir.iterdir())
        if child.is_dir() and common_git_dir(child) is not None
    ]
    if not candidates:
        stdout.write(f"No git repositories found under {scan_dir}.\n")
        return 0
    if take_all:
        chosen = candidates
    else:
        options = []
        for child in candidates:
            hooks_dir = hooks_directory(child)
            has_hook = hooks_dir is not None and (hooks_dir / HOOK_NAME).is_file()
            options.append(
                prompt.Option(str(child), "already has a commit-msg hook" if has_hook else "")
            )
        try:
            picked = prompt.multiselect(
                "Add a commit-msg hook to which repositories?",
                options, (), stdin=stdin, stdout=stdout,
            )
        except prompt.Cancelled:
            stdout.write("Nothing selected; nothing changed.\n")
            return 0
        chosen = [candidates[i] for i in picked]
    if not chosen:
        stdout.write("Nothing selected; nothing changed.\n")
        return 0
    result = 0
    for child in chosen:
        result |= _cmd_add_one(child.resolve(), yes=yes, stdin=stdin, stdout=stdout)
    return result


def _cmd_remove(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    if args.all and args.paths:
        print("error: --all takes no paths", file=sys.stderr)
        return 64
    if args.all:
        return remove_entries(verified_entries(), yes=args.yes, stdin=stdin, stdout=stdout)
    paths = [Path(p) for p in args.paths] or [Path.cwd()]
    entries = verified_entries()
    result = 0
    for path in paths:
        cwd = path.resolve()
        if common_git_dir(cwd) is None:
            stdout.write(f"skipped  {cwd} is not a git repository\n")
            result |= 1
            continue
        entry = _entry_for(cwd, entries)
        if entry is None:
            stdout.write(f"skipped  {cwd} is not a git repository\n")
            result |= 1
            continue
        hook_file = Path(entry["hooks_dir"]) / entry["hook"]
        try:
            content = hook_file.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if MARKER_PHRASE not in content and entry.get("state") == "unrecorded":
            stdout.write(f"none  {cwd}: no CopyDesk hook found\n")
            result |= 1
            continue
        forgotten: set[str] = set()
        try:
            if remove_entry(entry, entries, yes=args.yes, stdin=stdin, stdout=stdout):
                forgotten.add(entry["git_dir"])
        except OSError as error:
            stdout.write(f"error  {entry['path']}: {error}\n")
            result |= 1
        if forgotten:
            forget_entries(forgotten)
    return result


def _cmd_list(stdout: TextIO) -> int:
    entries = verified_entries()
    if not entries:
        stdout.write("No repositories recorded. `copydesk hook add` records one.\n")
        return 0
    for entry in entries:
        stdout.write(f"{entry.get('state', '?'):20} {entry.get('path', '?')}\n")
    return 0


def run(argv: list[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="copydesk hook", description="Manage the commit-msg hook across repositories."
    )
    subcommands = parser.add_subparsers(dest="action")

    add_parser = subcommands.add_parser("add", help="install the hook and record the repository")
    add_parser.add_argument("paths", nargs="*", help="repositories to add (default: here)")
    add_parser.add_argument("--scan", metavar="DIR", help="offer the repositories one level under DIR")
    add_parser.add_argument("--all", action="store_true", help="with --scan, take every repository found without asking")
    add_parser.add_argument("--yes", "-y", action="store_true", help="skip the chaining question for foreign hooks")

    remove_parser = subcommands.add_parser("remove", help="remove the hook and forget the repository")
    remove_parser.add_argument("paths", nargs="*", help="repositories to remove (default: here)")
    remove_parser.add_argument("--all", action="store_true", help="remove every recorded repository")
    remove_parser.add_argument("--yes", "-y", action="store_true", help="remove shared hooks without asking")

    subcommands.add_parser("list", help="report every recorded repository")

    args = parser.parse_args(argv)
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout

    if args.action == "add":
        return _cmd_add(args, in_stream, out_stream)
    if args.action == "remove":
        return _cmd_remove(args, in_stream, out_stream)
    if args.action == "list":
        return _cmd_list(out_stream)
    parser.print_help(sys.stderr)
    return 64
