#!/usr/bin/env python3
"""The style shelf and the floor.

A style is a preset. `extends` chains compose them, which is machinery that
already exists. The floor is the set of clauses no style choice can remove.
"""

from __future__ import annotations

STYLE_NAMES = ("plain", "general", "engineer", "editorial")

# 0.1.0 shipped one preset under this name. The alias is kept indefinitely.
STYLE_ALIASES = {"plain-english": "plain"}

# One line per style, shown beside its name wherever a reader picks one. The
# text is the preset's own `description` field, copied rather than read, so
# the gate's import path opens no JSON. tests/test_styles.py fails if a
# preset's description moves and this does not.
DESCRIPTIONS = {
    "plain": "Structured but plain writing — full technical content, simpler sentences, no AI-isms",
    "general": "Plain writing with nothing assumed. Every term is glossed.",
    "engineer": "Terse. One instruction per sentence, tables over prose.",
    "editorial": "Flowing short paragraphs. Structure is rare and deliberate.",
}

# Behavioural clauses. They have no config key, which is what immune means.
# Where a clause overlaps a pattern rule, the gate stays adjustable through
# `rules`, and doctor names the loosening.
FLOOR = {
    "answer-first": "Answer first, in every channel with a reader waiting.",
    "closing-block": (
        "A closing block appears only when a decision is blocked on the reader. "
        "An open question is never restated. One decisions block per piece of "
        "work, not per turn."
    ),
    "say-once": "Say a thing once. No soft offers, no AI-tells, no orphan pointers.",
}


class UnknownStyle(ValueError):
    """A style name that is not on the shelf."""


def preset_for(style: str) -> str:
    resolved = STYLE_ALIASES.get(style, style)
    if resolved not in STYLE_NAMES:
        shelf = ", ".join(STYLE_NAMES)
        raise UnknownStyle(f"{style!r} is not a style. The shelf is: {shelf}.")
    return resolved
