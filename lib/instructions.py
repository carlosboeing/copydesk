#!/usr/bin/env python3
"""Render one instruction set per channel.

The preset owns the rules. The gate reads them as token lists and thresholds.
The instructions state them as short prose. Neither is hand-edited, and the
token lists stay out of the prose: categories generalise where lists only
enumerate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import guidance
import styles

VERBOSITY_LEVELS = ("low", "medium", "high")
OUTPUT_STYLE_NAMES = ("CopyDesk low", "CopyDesk medium", "CopyDesk high")
FINGERPRINT_MARKER = "copydesk-build:"
RULES_START = "<!-- plain-english-rules:start -->"
RULES_END = "<!-- plain-english-rules:end -->"

# Who wrote a rendered copy, named in its provenance comment. Setup points
# at --repair because the way to regenerate an installed copy is to run it,
# and naming the repository's generator would send the reader to the wrong
# machine's files.
GENERATOR_WRITER = "scripts/generate-instructions.py"
SETUP_WRITER = "copydesk setup; regenerate with copydesk setup --repair"

# The $id the schema declares, and where SchemaStore will serve it.
SCHEMA_ID = "https://json.schemastore.org/copydesk.config.json"

# Flip to True in the release that follows the catalog entry merging.
SCHEMASTORE_MERGED = False

# What the wizard writes into every config it generates. The raw tag URL
# resolves the moment v0 exists; the catalog URL does not resolve until
# SchemaStore merges, and a 404 gives the user no autocomplete at all.
SCHEMA_URL = (
    SCHEMA_ID
    if SCHEMASTORE_MERGED
    else "https://raw.githubusercontent.com/carlosboeing/copydesk/v0/copydesk.schema.json"
)

# Measured, not guessed. A 1,256-word block did not hold across a long
# session; a 200-word one did. The budget is tested, not documented.
BUDGETS = {"chat": 220, "documents": 260, "commits": 25, "reviews": 25}

_STOPPING_RULES = (
    "If the first line answers it, stop. "
    "Cut any sentence that does not change what the reader knows or does. "
    "Assume the reader will ask for more."
)

_CHAT_STRUCTURE = (
    "In the terminal, number every section and bold its label. "
    "Put a horizontal rule between sections. Never nest a table inside a list."
)

_VERBOSITY_LINES = {
    "low": "Give the answer and one line of support.",
    "medium": "Give the answer, the trade-offs, and the next step.",
    "high": "Show the full reasoning.",
}

# Generic craft ships under every style. Specific templates are the
# repository's: frontmatter fields and required sections are project
# conventions, and CopyDesk points at them rather than owning them.
CRAFT = (
    "State the problem before the solution. A heading carries a claim, not a "
    "topic word. One idea per section. Order sections for a reader going from "
    "top to bottom. Follow the repository's own template where it has one."
)

_COMMITS = (
    "In a commit message: an imperative subject at most 72 characters, then a "
    "body saying why, not what the diff shows."
)

_REVIEWS = (
    "In a review comment: name the file and line, then say the fix."
)

# Extent only. The channel line already says what the body carries and the
# style line says what shape it takes, so a verbosity line that names a
# paragraph or a bullet list either repeats one of them or argues with it.
_COMMITS_VERBOSITY = {
    "low": "",
    "medium": "Keep the body to a few lines.",
    "high": "Let the body cover the reasoning in full.",
}

# The design's Part 4 table, one line per style per channel. Without these the
# style shelf changes only the gate's thresholds, and the model is never told
# how the user wants to be written to. Each line says what its channel line
# does not: the channel names the parts of the message, the style names their
# shape. Restating the channel line spends a word budget on nothing.
STYLE_LINES = {
    ("chat", "plain"): "Short sentences. Structure where it helps, prose where it does not.",
    ("chat", "general"): "Short sentences. Gloss every term the reader may not know.",
    ("chat", "engineer"): "Terse lists and tables. One instruction per sentence.",
    ("chat", "editorial"): "Flowing short paragraphs. Lists and tables are rare.",
    ("documents", "plain"): "Prose carries the reasoning. Structure carries the facts.",
    ("documents", "general"): "The explanatory document. Commonest words, nothing assumed.",
    ("documents", "engineer"): "Numbered procedures and tables. Minimal connecting prose.",
    ("documents", "editorial"): "Prose almost everywhere. Structure is rare and deliberate.",
    ("commits", "engineer"): "Body facts as bullets.",
    ("commits", "plain"): "Body as prose.",
    ("commits", "general"): "Body as prose, every term glossed.",
    ("commits", "editorial"): "Body as prose.",
    ("reviews", "plain"): "Write the fix as prose.",
    ("reviews", "general"): "Write the fix as prose, glossing every term.",
    ("reviews", "engineer"): "One line each.",
    ("reviews", "editorial"): "Write the fix as prose.",
}


def style_line(channel: str, style: str) -> str:
    """One style means the right thing in each channel, which is why the
    lookup is keyed by both."""
    return STYLE_LINES.get((channel, styles.preset_for(style)), "")


def resolve_verbosity(resolved: dict, environ: dict) -> str:
    """Environment, then the harness style picker, then the config.

    The style picker's choice reaches this function as the config value,
    because `copydesk set` writes it there. So two sources are read here and
    the third is a write that happened earlier.
    """
    declared = environ.get("COPYDESK_VERBOSITY")
    if declared in VERBOSITY_LEVELS:
        return declared
    settings = (resolved.get("channels") or {}).get("chat") or {}
    value = settings.get("verbosity", "low")
    return value if value in VERBOSITY_LEVELS else "low"


def render_output_style_body(resolved: dict, level: str) -> str:
    """The marked rules block of one output style, and nothing around it.

    The generator wraps this in frontmatter; the reminder re-renders it to
    compare against an installed file's fingerprint. Both must produce the
    same bytes from the same inputs, so there is one function.
    """
    return render_chat(with_verbosity(resolved, level))


def render_output_style(resolved: dict, level: str, writer: str) -> str:
    """One whole output style file: frontmatter, fingerprint marker, rules.

    The generator writes the shipped copies and the wizard writes the
    installed ones, so both call this and `writer` says which. Two wrappers
    is how an installed file came to be compared against a body nothing had
    produced.

    The description follows the configured chat style rather than the
    preset's own block: Claude Code's style picker reads this line, so it
    has to describe the body below it.
    """
    chat_style = ((resolved.get("channels") or {}).get("chat") or {}).get("style", "plain")
    style = (resolved.get("instructions") or {})["output_style"]
    body = render_output_style_body(resolved, level)
    return (
        "---\n"
        f"name: CopyDesk {level}\n"
        f"description: {styles.DESCRIPTIONS[styles.preset_for(chat_style)]}\n"
        f"keep-coding-instructions: {str(style['keep_coding_instructions']).lower()}\n"
        "---\n\n"
        f"<!-- Generated from rules/{resolved.get('id', 'plain')}.json"
        f" by {writer}. Do not edit by hand. -->\n"
        f"<!-- {FINGERPRINT_MARKER}{fingerprint(body)} -->\n\n"
        f"{RULES_START}\n{body}\n{RULES_END}\n"
    )


def with_verbosity(resolved: dict, level: str) -> dict:
    """A copy of the resolved config with chat's verbosity replaced."""
    copied = json.loads(json.dumps(resolved))
    copied.setdefault("channels", {}).setdefault("chat", {})["verbosity"] = level
    return copied


def word_count(text: str) -> int:
    return len(text.split())


def render_chat(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("chat") or {}
    # `config.resolve()` grafts its extra keys onto the preset document, so the
    # instructions sit at the top level. The nested `preset` form is what a
    # hand-built fixture produces, which is how reading only it went unnoticed.
    inst = resolved.get("instructions") or (resolved.get("preset") or {}).get("instructions") or {}
    parts = [
        _STOPPING_RULES,
        styles.FLOOR["answer-first"],
        styles.FLOOR["closing-block"],
        styles.FLOOR["say-once"],
        style_line("chat", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "low"), _VERBOSITY_LINES["low"]),
        inst.get("categories", ""),
        _CHAT_STRUCTURE,
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    return "\n\n".join(part for part in parts if part)


def render_documents(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("documents") or {}
    if not settings.get("enabled", True):
        return ""
    parts = [
        style_line("documents", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "high"), ""),
        CRAFT,
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    return "\n\n".join(part for part in parts if part)


def render_commits(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("commits") or {}
    if not settings.get("enabled", True):
        return ""
    parts = [
        _COMMITS,
        style_line("commits", settings.get("style", "engineer")),
        _COMMITS_VERBOSITY.get(settings.get("verbosity", "low"), ""),
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    return "\n\n".join(part for part in parts if part)


def render_reviews(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("reviews") or {}
    if not settings.get("enabled", False):
        return ""
    parts = [
        _REVIEWS,
        style_line("reviews", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "medium"), ""),
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    return "\n\n".join(part for part in parts if part)


def render_agents_block(resolved: dict) -> str:
    parts = [
        render_documents(resolved),
        render_commits(resolved),
        render_reviews(resolved),
    ]
    non_empty = [part for part in parts if part]
    if not non_empty:
        return ""
    body = "\n\n".join(non_empty)
    return f"<!-- copydesk:start -->\n{body}\n<!-- copydesk:end -->"


def fingerprint(rendered: str) -> str:
    """A hash of the rendered file itself, minus the marker line.

    Hashing the inputs would mean listing them: the preset, the user config,
    every style file, the guidance registry, the merge table, and this
    module's own text. Any input left off the list produces a file that is
    stale and says it is fresh. Hashing the output covers every input by
    construction, including a change to the renderer.
    """
    body = "\n".join(
        line for line in rendered.splitlines() if FINGERPRINT_MARKER not in line
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def delta(static: dict, effective: dict) -> Optional[str]:
    """One line naming where the live settings differ from the static file.

    Every setting that changes an instruction is compared, not just style and
    verbosity. A project turning a channel off, or turning `sources` on,
    changes what the model is told and must reach the reminder.
    """
    differences = []
    for name in ("chat", "documents", "commits", "reviews"):
        left = (static.get("channels") or {}).get(name) or {}
        right = (effective.get("channels") or {}).get(name) or {}
        if left.get("enabled", True) != right.get("enabled", True):
            differences.append(f"{name} is {'on' if right.get('enabled', True) else 'off'}")
            continue
        if not right.get("enabled", True):
            continue
        for key in ("style", "verbosity"):
            if left.get(key) != right.get(key):
                differences.append(f"{name} {key} is {right.get(key)}")
        left_guidance = left.get("guidance") or {}
        right_guidance = right.get("guidance") or {}
        for guidance_id in guidance.IDS:
            if bool(left_guidance.get(guidance_id)) != bool(right_guidance.get(guidance_id)):
                state = "on" if right_guidance.get(guidance_id) else "off"
                differences.append(f"{name} {guidance_id} is {state}")
    if not differences:
        return None
    return "Here: " + "; ".join(differences) + "."


