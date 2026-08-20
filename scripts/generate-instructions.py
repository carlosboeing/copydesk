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
sys.path.insert(0, str(BUNDLE_ROOT / "lib"))

import adapters
import channels
import config as config_mod
import guidance
import instructions
import styles

PRESET_PATH = BUNDLE_ROOT / "rules" / "plain.json"
OUTPUT_STYLES_DIR = BUNDLE_ROOT / "output-styles"
REMINDER = BUNDLE_ROOT / "hooks" / "reminder.sh"
SCHEMA_PATH = BUNDLE_ROOT / "copydesk.schema.json"

REMINDER_START = "cat << 'EOF'\n"
REMINDER_END = "\nEOF\n"


def _rule_schema(rule_id: str, preset: dict) -> dict:
    """One rule's settings: severity, word lists where they apply, thresholds."""
    properties = {
        "severity": {
            "type": "string",
            "enum": sorted(config_mod.SEVERITY_TO_INTERNAL),
            "description": "error blocks, warn reports, off disables.",
        }
    }
    if rule_id in config_mod.pattern_rule_ids(preset) or rule_id == "unglossed-term":
        properties["add"] = {"type": "array", "items": {"type": "string"},
                             "description": f"Entries added to {rule_id}'s word list."}
        properties["remove"] = {"type": "array", "items": {"type": "string"},
                                "description": f"Entries removed from {rule_id}'s word list."}
    for name, kind in config_mod.RULE_PARAMETERS.get(rule_id, {}).items():
        properties[name] = {"type": kind, "description": f"{rule_id} threshold: {name}."}
    return {
        "type": "object",
        "additionalProperties": False,
        "description": f"Settings for the {rule_id} rule.",
        "properties": properties,
    }


def _channel_schema(name: str) -> dict:
    return {
        "type": "object",
        "description": f"Settings for the {name} channel.",
        "additionalProperties": False,
        "properties": {
            "enabled": {"type": "boolean", "description": "Whether this channel's instructions and gate coverage apply.", "default": True},
            "style": {"type": "string", "enum": list(styles.STYLE_NAMES), "description": "How writing in this channel reads.", "default": "plain"},
            "verbosity": {"type": "string", "enum": list(instructions.VERBOSITY_LEVELS), "description": "How much this channel says."},
            "guidance": {
                "type": "object",
                "additionalProperties": False,
                "description": "Deliverables a reply in this channel must contain.",
                "properties": {
                    guidance_id: {"type": "boolean", "description": guidance.SNIPPETS[guidance_id]}
                    for guidance_id in guidance.IDS
                },
            },
            "match": {"type": "array", "items": {"type": "string"}, "description": "Globs assigning files to this channel."},
        },
    }


def _path_list_schema(action: str) -> dict:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": f"Gitignore-style globs to {action}. A leading ! re-includes.",
    }


def render_schema(preset: dict) -> str:
    """Build the schema from the registries the engine already reads.

    A hand-written copy would drift exactly the way the instructions once did.
    The preset is a parameter because the rule list comes from its pattern
    blocks, so a new pattern rule reaches the schema with no second edit.
    """
    document = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://json.schemastore.org/copydesk.config.json",
        "title": "CopyDesk configuration",
        "type": "object",
        "required": ["version"],
        # A tolerant root, so $schema and future keys never break an older
        # install. Nested objects are strict, so a typo squiggles.
        "properties": {
            "version": {"type": "integer", "enum": [1], "description": "The config schema version. Required."},
            "channels": {
                "type": "object",
                "additionalProperties": False,
                "description": "How each kind of writing reads.",
                "properties": {name: _channel_schema(name) for name in config_mod.CHANNEL_DEFAULTS},
            },
            "agents": {
                "type": "array",
                "description": "Which AI tools CopyDesk sets up.",
                "items": {"type": "string", "enum": sorted(adapters.REGISTRY)},
            },
            "paths": {
                "type": "object",
                "additionalProperties": False,
                "description": "Which files the gate judges.",
                "properties": {action: _path_list_schema(action) for action in channels.ACTIONS},
            },
            "gate": {
                "type": "object",
                "additionalProperties": False,
                "description": "Gate behaviour.",
                "properties": {
                    "retries": {
                        "type": "integer", "minimum": 1, "maximum": 5, "default": 3,
                        "description": "How many refusals before the gate lets the write through.",
                    }
                },
            },
            "rules": {
                "type": "object",
                "additionalProperties": False,
                "description": "Per-rule severity, word lists and thresholds.",
                # One schema per rule id, so a threshold typo squiggles and
                # autocomplete offers the parameters that rule actually takes.
                "properties": {
                    rule_id: _rule_schema(rule_id, preset)
                    for rule_id in config_mod.rule_ids(preset)
                },
            },
            "telemetry": {
                "type": "object",
                "additionalProperties": False,
                "description": "What CopyDesk records locally.",
                "properties": {
                    "events": {"type": "boolean", "default": True, "description": "Record one event per check."},
                    "saveText": {"type": "boolean", "default": False, "description": "Store the flagged text beside the event."},
                },
            },
            "extends": {
                "description": "Advanced: a custom preset chain.",
                "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            },
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


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
    resolved = config_mod.resolve(BUNDLE_ROOT / "rules")

    words = len(preset["instructions"]["reminder"].split())
    declared = preset["instructions"]["reminder_word_count"]
    if words != declared:
        print(f"error  the reminder is {words} words; the preset declares {declared}", file=sys.stderr)
        return 1

    OUTPUT_STYLES_DIR.mkdir(parents=True, exist_ok=True)

    targets = {
        **{
            OUTPUT_STYLES_DIR / f"copydesk-{level}.md": instructions.render_output_style(resolved, level)
            for level in instructions.VERBOSITY_LEVELS
        },
        REMINDER: render_reminder(preset, REMINDER.read_text(encoding="utf-8")),
        SCHEMA_PATH: render_schema(preset),
    }

    stale = [path for path, rendered in targets.items() if not path.is_file() or path.read_text(encoding="utf-8") != rendered]
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
