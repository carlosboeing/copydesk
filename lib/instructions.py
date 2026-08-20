#!/usr/bin/env python3
"""Render one instruction set per channel.

The preset owns the rules. The gate reads them as token lists and thresholds.
The instructions state them as short prose. Neither is hand-edited, and the
token lists stay out of the prose: categories generalise where lists only
enumerate.
"""

from __future__ import annotations

import json

import guidance
import styles

VERBOSITY_LEVELS = ("low", "medium", "high")
OUTPUT_STYLE_NAMES = ("CopyDesk low", "CopyDesk medium", "CopyDesk high")

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

# The design's Part 4 table, one line per style per channel. Without these the
# style shelf changes only the gate's thresholds, and the model is never told
# how the user wants to be written to.
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
    ("reviews", "plain"): "Name the file and line, then the fix, in prose.",
    ("reviews", "general"): "Name the file and line, then the fix. Gloss every term.",
    ("reviews", "engineer"): "Name the file and line, then the fix. One line each.",
    ("reviews", "editorial"): "Name the file and line, then the fix, in prose.",
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


def with_verbosity(resolved: dict, level: str) -> dict:
    """A copy of the resolved config with chat's verbosity replaced."""
    copied = json.loads(json.dumps(resolved))
    copied.setdefault("channels", {}).setdefault("chat", {})["verbosity"] = level
    return copied


def word_count(text: str) -> int:
    return len(text.split())


def render_chat(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("chat") or {}
    preset = resolved.get("preset") or {}
    parts = [
        _STOPPING_RULES,
        styles.FLOOR["answer-first"],
        styles.FLOOR["closing-block"],
        styles.FLOOR["say-once"],
        style_line("chat", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "low"), _VERBOSITY_LINES["low"]),
        (preset.get("instructions") or {}).get("categories", ""),
        _CHAT_STRUCTURE,
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    return "\n\n".join(part for part in parts if part)

