#!/usr/bin/env python3
"""Render one instruction set per channel.

The preset owns the rules. The gate reads them as token lists and thresholds.
The instructions state them as short prose. Neither is hand-edited, and the
token lists stay out of the prose: categories generalise where lists only
enumerate.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import guidance
import styles

VERBOSITY_LEVELS = ("low", "medium", "high")

# One installed file carries this name in its frontmatter. Three per-level
# files existed so Claude Code's style picker could act as a verbosity
# switch; nothing ever read the pick back, so setup writes the configured
# verbosity into a single file and migrates the retired names away.
OUTPUT_STYLE_NAME = "CopyDesk"
LEGACY_OUTPUT_STYLE_NAMES = ("CopyDesk low", "CopyDesk medium", "CopyDesk high")
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
# session; a 200-word one did. The budget is tested, not documented. Chat
# moved from 220 to 240 when the guidance default grew to four items
# (+14 words) and the gloss clause arrived (+15): the default render then
# measured 236 words. The vocabulary clause then absorbed the shorter gloss
# line and gave the jargon category a test, taking the block to 267. Nothing
# outside tests reads this table, so a budget below its own default fails
# test_the_chat_block_fits_its_budget, never an install. The floor's
# target-form clause then took the default render to 282: twenty-two words
# for the two standards that were shipping in the preset and reaching no
# rendered surface at all. Making structure conditional then cost 46 and
# took the default to 328: 26 for the structure-when-earned clause, 17 for
# the precedence clause, and 3 to put a condition on the terminal rendering
# rules that used to read as an unconditional order to add sections.
BUDGETS = {"chat": 336, "documents": 260, "commits": 25, "reviews": 25}

_STOPPING_RULES = (
    "If the first line answers it, stop. "
    "Cut any sentence that does not change what the reader knows or does. "
    "Assume the reader will ask for more."
)

# Rendering mechanics, not a reason to add structure. The floor's
# structure-when-earned clause decides whether a reply gets sections at all;
# these three say how to draw them once it does. Read unconditionally, the
# first sentence was an order to number the sections of a two-line answer.
_CHAT_STRUCTURE = (
    "Where a terminal reply uses sections, number each one and bold its label. "
    "Put a horizontal rule between sections. Never nest a table inside a list."
)

# Chat never reaches the gate, and no shipped word list anticipates the
# vocabulary a project coins. The one prevention surface that covers both
# is this clause. Fifteen words, measured.
# Prevention before glossing. A banned-word list holds what someone thought to
# add; it can never hold a word the model invents mid-sentence. The rule, the
# test and the allowed examples travel together, because the bare category
# "opaque jargon" gave no way to tell `race condition` from `seam`. The banned
# side stays a category: a test pins that the token list never reaches the
# chat block, which is what keeps this text short. Four sentences and no colon:
# the splitter treats a colon as a break, and this paragraph has to pass the
# same paragraph-length rule it ships.
VOCABULARY = (
    "Prefer the word your reader already uses and never invent one. Common domain "
    "vocabulary such as race condition or idempotent is fine. Anything you cannot "
    "source, say in plain English. A term you must use anyway is glossed on first "
    "use, meaning in the same sentence."
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

# The three questions name the three failures no regex catches, measured in
# one 17,629-word document written with every gate active: a coined term used
# 49 times and first defined 43% of the way down, one sentence pattern
# repeated 76 times, and an opening that deferred every claim to a later
# section. A long document is generated forward once and never read back,
# which is why the instruction is a second pass rather than another rule.
SELF_AUDIT = (
    "A draft past about 1,000 words gets one audit pass before you deliver it. "
    "Which term did you coin, and does the text define it where it first "
    "appears? Which sentence pattern did you repeat past the point a reader "
    "would notice? Does the opening state the answer, or defer it to a section "
    "further down?"
)

# Prohibitions alone give the model nothing to generate into, so it falls
# back on its own default form and dodges the listed words. One pair per
# rule, ranked by how often that rule fired in the worst document measured:
# verb-jargon 71, sentence-length 36, banned-word 3, orphan-pointer 2,
# paragraph-length 1. Every defect line is fenced, and the linter exempts
# fences, so the examples may quote what they ban.
EXAMPLES = """## Before and after

The rules name what to avoid. These pairs name what to write. The left line is
the defect the gate reported, and the right line is the same fact, allowed.

**verb-jargon**, 71 of the 146 findings. Name the actor and the literal action.

```diff
- The rule data sits beside the linter and travels with every installed copy.
+ The installer copies the rule data next to the linter.
```

**sentence-length**, 36 findings. The cap is 25 words. Cut at a clause boundary and let the second half stand alone.

```diff
- The gate refuses the write, prints the findings, records a telemetry event and
- returns a non-zero exit code so the calling harness knows the attempt failed.
+ The gate refuses the write and prints the findings. It exits non-zero, so the
+ harness knows the attempt failed.
```

**banned-word**, 3 findings. Replace the quality claim with the evidence you would have cited for it.

```diff
- This is a robust, comprehensive fix with a clean escape hatch.
+ The fix covers all four channels. Setting the severity to off disables it.
```

**orphan-pointer**, 2 findings. Replace the pointer with the thing it points at.

```diff
- As noted above, the latter option needs a schema migration.
+ Adding a fourth severity value needs a schema migration.
```

**paragraph-length**, 1 finding. Four sentences maximum. Split the paragraph, never merge the sentences.

```diff
- The gate compiles the preset. It scores the text. It sorts the findings.
- It prints them. It exits non-zero.
+ The gate compiles the preset. It scores the text. It sorts the findings.
+
+ It prints them, then exits non-zero.
```"""

# The rules delete patterns, and a model applying them with no counterweight
# deletes the specifics too. Five items, because a list past five stops being
# read.
PRESERVE = """## Keep these

Where a draft already does one of these, leave it alone. Losing one of them to a
rule costs the reader more than the finding that rule would have reported.

- A specific number, path, version or date. Write `146 findings in 17,629 words`, never `a high failure rate`.
- A named actor doing a named thing. `The installer copies the file` beats `the file is copied`.
- Uneven sentence length. A four-word sentence after a twenty-word one is the rhythm the rules ask for.
- An aside or a self-correction in parentheses, where it records real doubt.
- A hedge that marks real uncertainty. Deleting it manufactures confidence."""


def render_teaching() -> str:
    """The worked examples and the preserve list, in that order.

    Neither depends on the config: a Before/After pair for a rule is the same
    pair whatever style renders it. They ship where they are read once per
    session, never in the per-turn reminder, which is re-sent on every turn.
    """
    return EXAMPLES + "\n\n" + PRESERVE


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
    # ASD-STE100 moved to the floor, so every style names it. Restating it
    # here would make one style say the same thing twice.
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


def render_output_style(resolved: dict, writer: str) -> str:
    """One whole output style file: frontmatter, fingerprint marker, rules.

    The generator writes the shipped copy and the wizard writes the
    installed one, so both call this and `writer` says which. Two wrappers
    is how an installed file came to be compared against a body nothing had
    produced.

    The body renders at the config's own chat verbosity. The three
    per-level files this replaces differed by that one sentence, and the
    picker wiring that would have made the level reachable never existed.

    The description follows the configured chat style rather than the
    preset's own block: Claude Code's style picker reads this line, so it
    has to describe the body below it.

    The stamp covers the whole rendered file, its own line excepted, so
    frontmatter and provenance sit under the same hash as the rules body.
    A body-only stamp waved every frontmatter change through: fixes to it
    reached new installs while existing ones read as fresh forever.
    """
    chat_style = ((resolved.get("channels") or {}).get("chat") or {}).get("style", "plain")
    # `config.resolve()` grafts its extra keys onto the preset document; the
    # nested form is what a hand-built fixture produces.
    inst = resolved.get("instructions") or (resolved.get("preset") or {}).get("instructions") or {}
    style = inst["output_style"]
    body = render_chat(resolved)
    head = (
        "---\n"
        f"name: {OUTPUT_STYLE_NAME}\n"
        f"description: {styles.DESCRIPTIONS[styles.preset_for(chat_style)]}\n"
        f"keep-coding-instructions: {str(style['keep_coding_instructions']).lower()}\n"
        "---\n\n"
        f"<!-- Generated from rules/{resolved.get('id', 'plain')}.json"
        f" by {writer}. Do not edit by hand. -->\n"
        f"<!-- {FINGERPRINT_MARKER}"
    )
    # The teaching section stays outside the markers. What sits between them
    # is spliced into instruction files verbatim, and a second copy of the
    # examples in every one of those files is what the budget exists to stop.
    tail = f" -->\n\n{RULES_START}\n{body}\n{RULES_END}\n\n{render_teaching()}\n"
    # The placeholder sits on the marker line, which fingerprint drops, so
    # hashing this build hashes the finished file.
    return head + fingerprint(head + "0" * 12 + tail) + tail


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
        styles.FLOOR["target-form"],
        style_line("chat", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "low"), _VERBOSITY_LINES["low"]),
        inst.get("categories", ""),
        VOCABULARY,
        # The condition sits immediately above the rendering mechanics it
        # governs. Separated, the two read as unrelated instructions and the
        # mechanics win, because they are the concrete pair.
        styles.FLOOR["structure-when-earned"],
        _CHAT_STRUCTURE,
    ]
    parts.extend(guidance.render(settings.get("guidance") or {}))
    # Last, so it closes over every rule above it. A precedence clause stated
    # mid-block reads as governing only its neighbours.
    parts.append(styles.FLOOR["precedence"])
    return "\n\n".join(part for part in parts if part)


def render_documents(resolved: dict) -> str:
    settings = (resolved.get("channels") or {}).get("documents") or {}
    if not settings.get("enabled", True):
        return ""
    parts = [
        style_line("documents", settings.get("style", "plain")),
        _VERBOSITY_LINES.get(settings.get("verbosity", "high"), ""),
        CRAFT,
        SELF_AUDIT,
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


def render_agents_block(resolved: dict, include_chat: bool = False) -> str:
    """The body an instruction file carries between CopyDesk's markers.

    Chat joins only where no other surface delivers it: Claude Code reads
    chat through the output style, so a file only Claude Code reads omits it
    here. Including chat brings the behavioural clauses with it — the chat
    renderer emits all three — which is why one flag covers both.

    `channels.chat.enabled` still applies. The check lives here, not in
    `render_chat`, because the output style calls that renderer for its
    rules region and must keep producing the same bytes.

    The markers themselves are not added here. Splicing and removal live in
    the module that owns the region pattern, so what this returns is exactly
    what goes between the markers.
    """
    chat = (resolved.get("channels") or {}).get("chat") or {}
    parts = (
        [render_chat(resolved)]
        if include_chat and chat.get("enabled", True)
        else []
    )
    parts.extend([
        render_documents(resolved),
        render_commits(resolved),
        render_reviews(resolved),
    ])
    # Guidance only. A toggle on in two channels wrote its line twice
    # once the channels joined; style, verbosity and craft stay per
    # channel because the same extent line is a different instruction
    # beside each channel-naming line. A merge from one channel also
    # replaces a member snippet from another, matching MERGES.
    guidance_texts = set(guidance.SNIPPETS.values()) | set(guidance.MERGES.values())
    seen: set[str] = set()
    paragraphs: list[str] = []
    for part in parts:
        for paragraph in part.split("\n\n"):
            if not paragraph:
                continue
            if paragraph in guidance_texts:
                if paragraph in seen:
                    continue
                seen.add(paragraph)
            paragraphs.append(paragraph)
    return "\n\n".join(guidance.collapse_members(paragraphs))


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


