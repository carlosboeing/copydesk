#!/usr/bin/env python3
"""Install CopyDesk gates a consumer runs in their own repository.

`copydesk install --git-hook` writes the pre-commit hook that checks staged
Markdown. The hook file on disk is the truth: a file carrying MARKER_PHRASE
belongs to CopyDesk and is refreshed in place, a file without it belongs to
the repository and is never touched - the installer prints the lines to
append instead, the same policy the commit-msg gate follows.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, TextIO

import hook

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

HOOK_NAME = "pre-commit"

# The line that says a pre-commit hook is CopyDesk's. Install refreshes a
# hook carrying it; anything else is left alone.
MARKER_PHRASE = "CopyDesk pre-commit gate"

BLOCK_START = "# >>> CopyDesk pre-commit gate >>>"
BLOCK_END = "# <<< CopyDesk pre-commit gate <<<"

HOOK_TEMPLATE = f"""#!/bin/sh
# {MARKER_PHRASE}
# Checks the Markdown this commit adds, judged against HEAD line by line,
# so a pre-existing error in the file never blocks this commit.
#
# Exit 1 refuses the commit. To commit anyway, run:
#
#   git commit --no-verify
#
# Fail open: a missing copydesk binary or an internal error lets the commit
# through rather than bricking the repository. Set COPYDESK_BIN to point at
# a copydesk binary that is not on PATH.

COPYDESK="${{COPYDESK_BIN:-copydesk}}"
if command -v "$COPYDESK" >/dev/null 2>&1 || [ -x "$COPYDESK" ]; then
  status=0
  "$COPYDESK" check --staged || status=$?
  if [ "$status" -eq 1 ]; then exit 1; fi
  if [ "$status" -gt 1 ]; then echo "copydesk: exit $status; commit allowed" >&2; fi
else
  echo "copydesk: not found on PATH; skipping the prose check" >&2
fi
exit 0
"""

# The block appended to a foreign hook on request. Its last command restores
# the host script's status, so a foreign refusal survives the chain; the
# status capture tolerates a host running under set -e.
CHAINED_BLOCK = f"""{BLOCK_START}
foreign=$?
COPYDESK="${{COPYDESK_BIN:-copydesk}}"
if command -v "$COPYDESK" >/dev/null 2>&1 || [ -x "$COPYDESK" ]; then
  status=0
  "$COPYDESK" check --staged || status=$?
  if [ "$status" -eq 1 ]; then exit 1; fi
fi
exit "$foreign"
{BLOCK_END}
"""


def _chain_instructions(target: Path) -> str:
    lines = "\n".join(f"             {line}" for line in CHAINED_BLOCK.rstrip("\n").split("\n"))
    return (
        f"skipped  {target} already exists\n"
        f"         To chain CopyDesk into it, add these lines at the end:\n"
        f"{lines}"
    )


def _install_git_hook(cwd: Path, stdout: TextIO) -> int:
    hooks_dir = hook.hooks_directory(cwd)
    if hooks_dir is None:
        print("error: not a git repository (git rev-parse found no hooks directory)", file=sys.stderr)
        return 64
    hook_file = hooks_dir / HOOK_NAME

    if not hook_file.is_file():
        try:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_file.write_text(HOOK_TEMPLATE, encoding="utf-8")
            hook_file.chmod(0o755)
        except OSError as error:
            print(f"error: cannot write {hook_file}: {error}", file=sys.stderr)
            return 1
        stdout.write(f"installed  {hook_file}\n")
        stdout.write("           staged Markdown is checked against the lines it adds;\n")
        stdout.write("           git commit --no-verify skips the check.\n")
        return 0

    try:
        content = hook_file.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: cannot read {hook_file}: {error}", file=sys.stderr)
        return 1

    if BLOCK_START in content:
        stdout.write(f"already chained  {hook_file}\n")
        return 0

    if MARKER_PHRASE in content:
        if content == HOOK_TEMPLATE:
            try:
                executable = bool(hook_file.stat().st_mode & 0o111)
                hook_file.chmod(0o755)
            except OSError as error:
                print(f"error: cannot update {hook_file}: {error}", file=sys.stderr)
                return 1
            if executable:
                stdout.write(f"already installed  {hook_file}\n")
            else:
                stdout.write(f"restored the executable bit  {hook_file}\n")
            return 0
        try:
            hook_file.write_text(HOOK_TEMPLATE, encoding="utf-8")
            hook_file.chmod(0o755)
        except OSError as error:
            print(f"error: cannot update {hook_file}: {error}", file=sys.stderr)
            return 1
        stdout.write(f"updated  {hook_file}\n")
        return 0

    # A hook someone else wrote: never overwrite. Print what chaining looks like.
    stdout.write(_chain_instructions(hook_file) + "\n")
    return 0


def run(argv: list[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="copydesk install",
        description="Install CopyDesk gates into this repository.",
    )
    parser.add_argument(
        "--git-hook", action="store_true",
        help="install the pre-commit hook that checks staged Markdown",
    )
    args = parser.parse_args(argv)
    out_stream = stdout or sys.stdout

    if not args.git_hook:
        parser.print_usage(sys.stderr)
        print(
            "error: choose what to install. Today that is --git-hook, "
            "the pre-commit hook checking staged Markdown.",
            file=sys.stderr,
        )
        return 64

    cwd = Path.cwd()
    return _install_git_hook(cwd, out_stream)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
