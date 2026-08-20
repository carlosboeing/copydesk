#!/usr/bin/env python3
"""Guidance ids name deliverables a reply must contain.

They are booleans per channel. On means the snippet rides in that channel's
instructions; off means it does not exist. Nothing warns or blocks, because
most of these are not lintable, and pretending otherwise teaches users the
config is theatre.

Placement belongs to the floor, not to any snippet. Position and next step
open the reply beside the answer; the closing block stays reserved for what
needs the reader.
"""

from __future__ import annotations

IDS = (
    "recommendations",
    "direction",
    "progress",
    "pushback",
    "alternatives",
    "assumptions",
    "estimates",
    "sources",
    "summary",
    "verification",
)

SNIPPETS = {
    "recommendations": (
        "When a question or a choice is open, give a proposed answer and one "
        "reason for it. Never present the options and stop there."
    ),
    "direction": (
        "When work continues past this reply, name the next step. Do not end "
        "on a step you are about to take yourself."
    ),
    "progress": (
        "When a task spans turns, state position in one line, as in step 3 of "
        "5, next is the backfill. Never list work already done."
    ),
    "pushback": (
        "Before agreeing with a premise that was challenged, give the "
        "strongest counter-argument you can, or say there is no counter-case."
    ),
    "alternatives": (
        "When you propose a solution, rank the alternatives with one line of "
        "trade-off each, and put the one you pick first."
    ),
    "assumptions": (
        "When you act under ambiguity, state the assumption you are acting on "
        "before the work, not after it."
    ),
    "estimates": (
        "Give an estimate in concrete units, as in about 15 minutes if tests "
        "cover this, an afternoon if not. Never say some work."
    ),
    "sources": (
        "Put the source beside a factual claim: file and line for code, a "
        "link for the web. A claim with no source is a guess."
    ),
    "summary": (
        "When a document runs long, open it with a three-sentence abstract "
        "saying what it is, what it decides, and who it is for."
    ),
    "verification": (
        "When you claim something is done or working, say how you verified it, "
        "or say untested. Never let the claim stand alone."
    ),
}

# Combinations render merged text rather than concatenating. Two snippets
# saying overlapping things read as two rules and are followed as neither.
MERGES = {
    frozenset({"progress", "direction"}): (
        "When work spans turns, open with position and what comes next, as in "
        "step 3 of 5, next is the backfill. Never list the work already done."
    ),
    frozenset({"recommendations", "alternatives"}): (
        "When a question or a choice is open, give ranked options with one "
        "line of trade-off each, your pick first, and the reason for it."
    ),
}


def render(active: dict) -> list[str]:
    """The merged snippets for one channel, in registry order."""
    on = [name for name in IDS if active.get(name)]
    consumed: set = set()
    rendered: list[str] = []
    for name in on:
        if name in consumed:
            continue
        merged = None
        for combination, text in MERGES.items():
            if name in combination and combination.issubset(on):
                merged = (combination, text)
                break
        if merged is not None:
            consumed |= merged[0]
            rendered.append(merged[1])
        else:
            consumed.add(name)
            rendered.append(SNIPPETS[name])
    return rendered
