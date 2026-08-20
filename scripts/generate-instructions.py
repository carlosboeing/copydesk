#!/usr/bin/env python3
"""Render CopyDesk's prose instructions from the preset document.

The canonical rules used to live as prose in a design document, with three
copies drifting apart. The preset owns them now, and this script writes the
copies, so they stop being copies.

    python3 scripts/generate-instructions.py           # write the instructions
    python3 scripts/generate-instructions.py --check   # exit 1 if they differ

Continuous integration runs --check, so a hand-edited instruction set fails the build
rather than reaching a release.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
PRESET_PATH = BUNDLE_ROOT / "rules" / "plain.json"
OUTPUT_STYLE = BUNDLE_ROOT / "output-styles" / "plain-english.md"
REMINDER = BUNDLE_ROOT / "hooks" / "reminder.sh"

RULES_START = "<!-- plain-english-rules:start -->"
RULES_END = "<!-- plain-english-rules:end -->"
REMINDER_START = "cat << 'EOF'\n"
REMINDER_END = "\nEOF\n"


def render_output_style(preset: dict) -> str:
    style = preset["instructions"]["output_style"]
    return (
        "---\n"
        f"name: {style['name']}\n"
        f"description: {style['description']}\n"
        f"keep-coding-instructions: {str(style['keep_coding_instructions']).lower()}\n"
        "---\n"
        "\n"
        f"<!-- Generated from rules/{preset['id']}.json by scripts/generate-instructions.py. Do not edit by hand.\n"
        f"     CopyDesk owns the canonical rules; this file is one instruction set of the {preset['id']} preset. -->\n"
        "\n"
        f"{RULES_START}\n"
        f"{preset['instructions']['rules_block']}\n"
        f"{RULES_END}\n"
    )


def render_reminder(preset: dict, existing: str) -> str:
    """Replace only the here-document. The surrounding script is not generated."""
    head, rest = existing.split(REMINDER_START, 1)
    _, tail = rest.split(REMINDER_END, 1)
    return head + REMINDER_START + preset["instructions"]["reminder"] + REMINDER_END + tail


def main() -> int:
    parser = argparse.ArgumentParser(description="Render CopyDesk's prose instructions from the preset.")
    parser.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    args = parser.parse_args()

    preset = json.loads(PRESET_PATH.read_text(encoding="utf-8"))

    words = len(preset["instructions"]["reminder"].split())
    declared = preset["instructions"]["reminder_word_count"]
    if words != declared:
        print(f"error  the reminder is {words} words; the preset declares {declared}", file=sys.stderr)
        return 1

    targets = {
        OUTPUT_STYLE: render_output_style(preset),
        REMINDER: render_reminder(preset, REMINDER.read_text(encoding="utf-8")),
    }

    stale = [path for path, rendered in targets.items() if path.read_text(encoding="utf-8") != rendered]
    if args.check:
        for path in stale:
            print(f"error  {path.relative_to(BUNDLE_ROOT)} differs from the preset", file=sys.stderr)
        if stale:
            print("       run: python3 scripts/generate-instructions.py", file=sys.stderr)
        return 1 if stale else 0

    for path in stale:
        path.write_text(targets[path], encoding="utf-8")
        print(f"wrote {path.relative_to(BUNDLE_ROOT)}")
    if not stale:
        print("instructions already match the preset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
