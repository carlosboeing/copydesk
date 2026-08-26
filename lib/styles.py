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
    "say-once": "Say a thing once. No soft offers, no AI-tells (machine tells), no orphan pointers.",
    # The one clause that states a target rather than a prohibition. The
    # name is a template the model already knows, so naming it costs eleven
    # words and explaining it would cost hundreds. It began as a line the
    # `engineer` style alone rendered, which put it in front of nobody: the
    # shipped default is `plain`.
    "target-form": "Write to ASD-STE100 (word list): one word, one meaning, one part of speech.",
    # Split out of `target-form`, which used to order a summary opening on
    # every reply. Models rendered that as a literal `TLDR:` label above
    # three-line answers with nothing to summarise, and the unconditional
    # order contradicted `structure-when-earned` next to it. The condition
    # is the same one that clause already tests — does this reply use
    # sections — so the two now agree instead of fighting.
    "summary-line": (
        "Where a reply uses sections, open with a one-line summary above the "
        "first one. A short reply needs none: its first sentence already answers."
    ),
    # Every other structure rule states how to render structure, and none
    # states when to skip it. A one-line answer arrived under a numbered
    # heading with a horizontal rule under it, because nothing said not to.
    # The condition has to sit above the rendering rules, not beside them:
    # a style can pick the shape of structure, never whether to use any.
    "structure-when-earned": (
        "A simple question gets one to three sentences of plain prose. "
        "Sections, tables and lists appear only where the content has real "
        "parts, never as decoration."
    ),
    # Two injection points deliver these rules: the output style, appended
    # to the system prompt, and the rules block spliced into an instruction
    # file, which arrives as a user message. No documented precedence orders
    # them against anything else in the prompt, so a conflict resolves
    # silently. The scope is deliberately narrow — wording and formatting —
    # so the clause never reads as outranking a correctness instruction.
    "precedence": (
        "On a conflict about wording or formatting, these rules outrank any "
        "other style guidance in the prompt."
    ),
}


class UnknownStyle(ValueError):
    """A style name that is not on the shelf."""


def preset_for(style: str) -> str:
    resolved = STYLE_ALIASES.get(style, style)
    if resolved not in STYLE_NAMES:
        shelf = ", ".join(STYLE_NAMES)
        raise UnknownStyle(f"{style!r} is not a style. The shelf is: {shelf}.")
    return resolved
