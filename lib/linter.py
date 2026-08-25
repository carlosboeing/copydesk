#!/usr/bin/env python3
"""CopyDesk Markdown linter.

Vendored and adapted from ``evals/ste_lint.py`` in
https://github.com/AminBlg/SimpleEnglish at commit
59bf6702197a5aadc96d197ea17f290d8d50dcd3.

Upstream licence: MIT License, Copyright (c) 2026 AminBlg.
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: the above copyright
notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The whitespace tokenizer, sentence splitter, exclusion approach, and
line-oriented reporting come from the upstream implementation. This project
replaces its ASD-STE100 rule list. It deliberately drops upstream bans on
contractions, modal verbs, and semicolons because these rules permit all three.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as _replace_field
import datetime
import difflib
import fcntl
import hashlib
import json
from math import sqrt
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Iterable, NamedTuple, Optional, Union

import channels


FILE_STATISTICS_MIN_SENTENCES = 15
LONG_SENTENCE_WARNING_WORDS = 25
LONG_SENTENCE_ERROR_WORDS = 40
LONG_SENTENCE_RATE = 0.10
EM_DASH_RATE_DEFAULT = 4.0
AVG_SENTENCE_MIN_WORDS = 12
AVG_SENTENCE_MAX_WORDS = 20
MIN_SENTENCE_VARIATION = 4.0
PARAGRAPH_MAX_SENTENCES = 4
LIST_EXEMPTION_RATIO = 0.5
RETRY_LIMIT = 3
CHAT_GATE_DEFAULT = "warn"
STATE_TTL_SECONDS = 24 * 60 * 60
# The hook registry shares the state directory with retry session state, and
# the sweeper below takes stale *.json files. The registry outlives every
# session, so the sweeper skips this name. hook.py reads it from here rather
# than naming the file itself, because a second spelling is a deletion.
HOOK_REGISTRY_NAME = "hooks.json"
ROTATION_SIZE_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_STORED_FINDINGS = 20
# Inner character-level SequenceMatcher is quadratic. Replaced blocks larger
# than this fall back to the whole block so the PreToolUse hook cannot stall.
# Over-attribution blocks rather than fail-open.
INNER_DIFF_CHAR_CAP = 4096
# The outer line-level matcher is quadratic in line count for the same reason.
# Measured on one machine: 800 lines take 0.13 s, 1,600 take 0.98 s, 3,200 take
# 7.96 s and 6,400 take 63.5 s — past Claude Code's 60 s hook timeout, which
# kills the gate and lets the write through unchecked. The largest Markdown
# file in this repository is 342 lines, so the cap sits far above real
# documents and still bounds the worst case near a second.
OUTER_DIFF_LINE_CAP = 2000
# Whitespace-delimited words emitted by hooks/reminder.sh on every user prompt.
# tests/test_telemetry.py reads the hook and fails if this drifts from the text.
REMINDER_WORD_COUNT = 61


@dataclass(frozen=True)
class Finding:
    """One line-oriented check result for the command or hook.

    ``span_start`` and ``span_end`` locate the flagged text inside the masked
    document, so edit attribution can compare characters rather than guess
    from line numbers. A finding without a span is unattributable by overlap
    and is marked existing; document-scoped rules can still block when they
    newly fire.
    """

    line: int
    check: str
    excerpt: str
    severity: str
    origin: str = "new"
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    # Ranges to test instead of the outer span, for a rule measured over parts
    # of a paragraph that are not contiguous. paragraph-length excludes list
    # item lines from its count, so a bullet between two counted sentences sits
    # inside the outer span and must not own the finding.
    spans: tuple = ()

    def render(self) -> str:
        return f"{self.line}:{self.check}:{self.excerpt}"


@dataclass(frozen=True)
class Sentence:
    """A sentence produced by the upstream splitter, with its source line."""

    text: str
    line: int
    start: int = 0
    end: int = 0

    @property
    def words(self) -> int:
        # Keep SimpleEnglish's whitespace tokenizer.
        return len(self.text.split())


@dataclass(frozen=True)
class RulePattern:
    """A quoted rules-block phrase and the check that enforces it."""

    phrase: str
    regex: re.Pattern[str]
    check: str
    severity: str


def _compiled(expression: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(expression, flags)


# The rule inventory is data. rules/<preset>.json owns it, and this module
# compiles it at import. tests/test_rules_sync.py verifies that every executable
# token reaches the compiled inventory and that the generated instructions match.
#
# Compilation order is the preset's order, and it is stable, because lint()
# sorts findings by line, severity, check and excerpt.


try:
    import config
except ImportError:  # pragma: no cover - the linter still lints without a cascade
    config = None

try:
    import instructions
except ImportError:
    instructions = None


def _preset_path() -> Path:
    """Find the preset document.

    Three locations, in order. The installed hook copy sits beside linter.py
    with no bundle around it, so that case is searched explicitly rather than
    left to fail.
    """
    override = os.environ.get("COPYDESK_RULES")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    candidates = (
        here.parents[1] / "rules" / "plain.json",   # source bundle
        here.parent / "rules" / "plain.json",       # installed beside linter.py
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


class PresetNotFound(RuntimeError):
    """The rule data is missing, so there is nothing to compile."""


def load_preset(path: Optional[Path] = None) -> dict:
    target = path or _preset_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PresetNotFound(
            f"CopyDesk cannot find its rule data at {target}. "
            "Install rules/plain-english.json beside linter.py, or point "
            "COPYDESK_RULES at the preset."
        ) from error


def _anchor(scope: str, body: str) -> str:
    """Wrap a token body in the anchors its scope implies.

    Token bodies are regular expressions, matching Vale's `existence` tokens.
    A plain word is a regular expression matching itself, so adding one needs
    no regex knowledge.
    """
    if scope == "word":
        return r"\b" + body + r"\b"
    if scope == "line-initial":
        return r"(?m)^[ \t]*" + body
    if scope == "raw":
        return body
    raise ValueError(f"unknown pattern scope: {scope!r}")


def compile_patterns(preset: dict) -> tuple[RulePattern, ...]:
    compiled: list[RulePattern] = []
    for block in preset["patterns"]:
        if block.get("kind", "existence") != "existence":
            raise ValueError(f"unsupported pattern kind: {block.get('kind')!r}")
        if block["severity"] == "off":
            continue
        flags = re.IGNORECASE if block.get("ignorecase", True) else 0
        for token in block["tokens"]:
            phrase = token if isinstance(token, str) else token["phrase"]
            body = token if isinstance(token, str) else token["match"]
            compiled.append(
                RulePattern(
                    phrase,
                    re.compile(_anchor(block.get("scope", "word"), body), flags),
                    block["id"],
                    block["severity"],
                )
            )
    return tuple(compiled)


PRESET = load_preset()
RULE_PATTERNS: tuple[RulePattern, ...] = compile_patterns(PRESET)

# Resolved presets, keyed by the config files that produced them. The gate runs
# on every Write and Edit, so re-reading and re-compiling per document would be
# a latency cost on the measured 17 ms median.
_PRESET_CACHE: dict[tuple, tuple[dict, tuple[RulePattern, ...]]] = {}
_REPORTED_CONFIG_ERRORS: set[str] = set()


def _rules_dir() -> Path:
    return _preset_path().parent


def effective_preset(path: Optional[Union[str, Path]] = None, *, channel: Optional[str] = None) -> tuple[dict, tuple[RulePattern, ...]]:
    """The preset for one document, after the config cascade.

    Returns the (preset_dict, compiled_patterns) tuple that applies to `path`.

    Fails open. A config error is reported once and the built-in preset is
    used, because a gate that blocks on its own misconfiguration is worse
    than one that lets the write through.

    `channel` overrides path routing for a gate that has no file to route,
    as the chat Stop hook does; the chat channel's style then picks its
    preset exactly as it would for a routed document.
    """
    if config is None:
        return PRESET, RULE_PATTERNS
    try:
        user = config.user_config_path()
        project = config.project_config_path(path) if path else None
        local = config.local_config_path(path) if path else None
    except config.ConfigError as error:
        _report_config_error(str(error))
        return PRESET, RULE_PATTERNS

    root_key = str(project.parent) if project else (str(local.parent) if local else (str(Path(path).resolve().parent) if path else ""))
    files_key = tuple(
        (str(p), p.stat().st_mtime_ns) if p else None
        for p in (user, project, local)
    )
    routing_key = (root_key, files_key, None)
    routing_cached = _PRESET_CACHE.get(routing_key)
    if routing_cached is not None:
        routing_resolved, _ = routing_cached
    else:
        try:
            routing_resolved = config.resolve(_rules_dir(), path, user_path=user, project_path=project, local_path=local)
            routing_compiled = compile_patterns(routing_resolved)
            _PRESET_CACHE[routing_key] = (routing_resolved, routing_compiled)
        except config.ConfigError as error:
            _report_config_error(str(error))
            return PRESET, RULE_PATTERNS

    resolved_channel = channel or (channels.decide(str(path), routing_resolved).channel if path else None)

    full_key = (root_key, files_key, resolved_channel)
    cached = _PRESET_CACHE.get(full_key)
    if cached is not None:
        return cached

    try:
        resolved = config.resolve(
            _rules_dir(), path, user_path=user, project_path=project, local_path=local, channel=resolved_channel
        )
        compiled = compile_patterns(resolved)
    except config.ConfigError as error:
        _report_config_error(str(error))
        return PRESET, RULE_PATTERNS

    _PRESET_CACHE[full_key] = (resolved, compiled)
    return resolved, compiled


def _report_config_error(message: str) -> None:
    """Say so, once per message, and record it. Never block."""
    if message in _REPORTED_CONFIG_ERRORS:
        return
    _REPORTED_CONFIG_ERRORS.add(message)
    print(f"copydesk config error  {message}", file=sys.stderr)
    try:
        _record_event({"event": "config_error", "message": message})
    except Exception:
        pass


def _rule_severity(preset: dict, rule_id: str, default: str) -> str:
    """Config vocabulary in, internal vocabulary out."""
    entry = (preset.get("rules") or {}).get(rule_id) or {}
    declared = entry.get("severity")
    if declared is None:
        return default
    return config.SEVERITY_TO_INTERNAL.get(declared, default) if config else default


def _rule_number(preset: dict, rule_id: str, key: str, default: float) -> float:
    """A configured threshold, or the built-in default. Never raises."""
    entry = (preset.get("rules") or {}).get(rule_id) or {}
    value = entry.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

# The rules block also quotes advisory instructions and worked examples that a
# regex must not enforce. Their exact text stays in the same inventory, so a
# rules-block edit cannot silently escape the sync test. RULE_PATTERNS remains
# the executable subset; test_checks.py proves its behavior separately.
CANONICAL_REFERENCE_PHRASES = tuple(PRESET["reference_phrases"])

PATTERN_TEXTS = tuple(pattern.phrase for pattern in RULE_PATTERNS) + CANONICAL_REFERENCE_PHRASES
AI_TELL_PHRASES = frozenset(PRESET["ai_tells"])


_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?^(?:---|\.\.\.)[ \t]*(?:\n|$)", re.MULTILINE | re.DOTALL)
_FENCED_CODE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*(?:\n|$)")
_BLOCKQUOTE = re.compile(r"(?m)^[ \t]*>.*(?:\n|$)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://[^\s)\]>]+")
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+.*(?:\n|$)")
_LIST_MARKER = re.compile(r"^\s*(?:[-*]|\d+\.)\s+", re.MULTILINE)
_LIST_LINE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:])\s+")
# A git trailer: `Token: value`, as `git interpret-trailers` reads the final
# block. Token is alphanumeric plus hyphen; the value must be non-empty.
_TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s+\S")


def _mask(match: re.Match[str]) -> str:
    """Replace excluded content without moving any later line number."""
    return "".join("\n" if character == "\n" else " " for character in match.group(0))


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped and "|" in stripped and (stripped.startswith("|") or stripped.count("|") >= 2))


def _mask_table_lines(text: str) -> str:
    return "".join(_mask_line(line) if _is_table_line(line) else line for line in text.splitlines(keepends=True))


def _mask_line(line: str) -> str:
    return "".join("\n" if character == "\n" else " " for character in line)


def exclude_markdown(text: str, *, exclude_tables: bool = True) -> str:
    """Mask source material that must not count as authored prose.

    Newlines remain in place, so callers share both exclusions and line numbers.
    """
    text = _FRONTMATTER.sub(_mask, text)
    text = _FENCED_CODE.sub(_mask, text)
    text = _BLOCKQUOTE.sub(_mask, text)
    text = _INLINE_CODE.sub(" CODESPAN ", text)
    text = _URL.sub(" URL ", text)
    text = _HEADING.sub(_mask, text)
    return _mask_table_lines(text) if exclude_tables else text


def strip_code(text: str) -> str:
    """Compatibility name for the upstream exclusion entry point."""
    return exclude_markdown(text)


def sentences(text: str) -> list[str]:
    """The splitter's sentences as bare strings."""
    return [record.text for record in _sentence_records(text)]


def _unit_start_lines(lines: list[str], subject_is_own_unit: bool) -> list[int]:
    """The line indexes where a structural unit begins.

    A line opening with a list marker begins one, and so does the first
    dedented line after list content — an item never continues into what
    follows. The commit subject is a unit when the caller says so, because
    Conventional Commit subjects carry no full stop by convention.
    """
    starts = {0}
    if subject_is_own_unit:
        starts.add(1)
    in_list = False
    for index, line in enumerate(lines):
        if _LIST_LINE.match(line):
            starts.add(index)
            in_list = True
            continue
        if not line.strip():
            continue
        if in_list and not line[:1].isspace():
            starts.add(index)
            in_list = False
    return sorted(starts)


def _list_item_lines(lines: list[str]) -> set[int]:
    """One-based numbers of the lines a list item owns, marker line included.

    A wrapped continuation shares its item's unit, so its sentences belong to
    the item, not to the paragraph's prose.
    """
    owned: set[int] = set()
    starts = _unit_start_lines(lines, False)
    for position, start in enumerate(starts):
        if not _LIST_LINE.match(lines[start]):
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        owned.update(range(start + 1, end + 1))
    return owned


def _sentence_records(text: str, *, subject_is_own_unit: bool = False) -> list[Sentence]:
    """Apply the punctuation splitter within structural units, with source lines.

    Issue 22: splitting on terminal punctuation alone concatenated an
    unpunctuated commit subject and every bullet after it into one sentence.
    Punctuation now splits inside a unit; structure splits between units.
    """
    lines = text.split("\n")
    line_offsets = []
    total = 0
    for line in lines:
        line_offsets.append(total)
        total += len(line) + 1
    ordered = _unit_start_lines(lines, subject_is_own_unit)
    records: list[Sentence] = []
    for position, start in enumerate(ordered):
        if start >= len(lines):
            # A commit subject marked as its own unit can sit past the last
            # line of a one-line message; the empty slice yielded nothing.
            continue
        end = ordered[position + 1] if position + 1 < len(ordered) else len(lines)
        segment = "\n".join(lines[start:end])
        normalized = _LIST_MARKER.sub(lambda match: " " * len(match.group(0)), segment)
        base = line_offsets[start]
        cursor = 0
        for part in _SENTENCE_SPLIT.split(normalized):
            offset = normalized.find(part, cursor)
            if offset < 0:
                continue
            cursor = offset + len(part)
            stripped = part.strip()
            if len(stripped.split()) < 2:
                continue
            leading = len(part) - len(part.lstrip())
            span_start = base + offset + leading
            line = start + normalized.count("\n", 0, offset + leading) + 1
            records.append(Sentence(stripped, line, span_start, span_start + len(stripped)))
    return records


def _excerpt(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[:limit - 1]}…"


def _line_excerpt(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return _excerpt(text[start:] if end < 0 else text[start:end])


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _list_ratio(text: str) -> float:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return sum(1 for line in lines if _LIST_LINE.match(line)) / len(lines)


def _document_is_exempt(path: Optional[Union[str, Path]], text: str, ratio: float = LIST_EXEMPTION_RATIO) -> bool:
    name = Path(path).name.lower() if path else ""
    if any(token in name for token in ("checklist", "changelog", "roadmap", "status", "toc", "table-of-contents")):
        return True
    return _list_ratio(text) > ratio


def _paragraph_findings(text: str, *, exempt: bool, severity: str = "error", max_sentences: int = PARAGRAPH_MAX_SENTENCES) -> Iterable[Finding]:
    if exempt:
        return ()
    findings: list[Finding] = []
    cursor = 0
    for paragraph in re.split(r"\n[ \t]*\n", text):
        position = text.find(paragraph, cursor)
        cursor = max(cursor, position + len(paragraph))
        if not paragraph.strip() or all(_LIST_LINE.match(line) for line in paragraph.splitlines() if line.strip()):
            continue
        # List items are structure, not the paragraph's prose: their density
        # has its own rule, and counting them padded every intro-plus-bullets
        # block past the cap once items became units of their own.
        item_lines = _list_item_lines(paragraph.split("\n"))
        paragraph_sentences = [
            record for record in _sentence_records(paragraph)
            if record.line not in item_lines
        ]
        if len(paragraph_sentences) > max_sentences:
            # The counted sentences are the right unit. An edit inside them
            # made the paragraph too long; an edit elsewhere did not, and that
            # is exactly what span overlap answers. Blaming the one word that
            # moved would be wrong, but the counted prose is not one word.
            #
            # The span stops short of the whole paragraph on purpose. List
            # items are excluded from the count above, so a span covering them
            # blames a bullet for sentences it was never measured against —
            # the issue 8 symptom, reintroduced one layer down.
            start = max(0, position)
            line = _line_number(text, start)
            counted = tuple(
                (start + record.start, start + record.end)
                for record in paragraph_sentences
            )
            findings.append(Finding(
                line, "paragraph-length", _excerpt(paragraph), severity,
                span_start=counted[0][0], span_end=counted[-1][1], spans=counted,
            ))
    return findings


# The two sentinels exclude_markdown() substitutes. Both are capitalised, neither
# is sentence-initial, and neither belongs to any vocabulary, so an unglossed-term
# rule that does not skip them reports every document. A scan over 182 Markdown
# files on 2026-08-19 counted 8,967 CODESPAN hits in 114 files.
_MASKING_SENTINELS = frozenset({"CODESPAN", "URL"})

# A candidate is a capitalised or all-caps token, keeping internal punctuation
# that real terms carry: macOS, Node.js, C++, well-formed hyphenations.
# Two characters minimum. A one-letter token is the pronoun I, a list label, or
# an initial, and none of them can carry a gloss.
_TERM_CANDIDATE = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:[.+#-][A-Za-z0-9]+)*\b")

# The three gloss forms the design accepts, tested against the sentence the term
# first appears in.
# An appositive follows the term: "Kubernetes, a container orchestrator, ...".
# Requiring a determiner keeps ", and then we left" from reading as a gloss.
_APPOSITIVE_TAIL = re.compile(r"^,\s+(?:a|an|the)\s+\S")
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")
_DEFINITION = re.compile(
    r"\b(?:is|are|was|were|means|stands for|refers to|short for)\b", re.IGNORECASE
)


def _term_is_sentence_initial(line: str, column: int) -> bool:
    """Decide sentence-start from the text, never from token position zero.

    _SENTENCE_SPLIT splits on punctuation followed by whitespace, so a bold
    marker after a full stop prevents the split and the next capital reads as
    mid-sentence. A list-item-initial capital is sentence-initial too: the
    2026-08-19 scan flagged Step, Modify, Create, Run and Add, every one a
    checklist verb opening a bullet.
    """
    before = line[:column]
    stripped = before.strip()
    if not stripped:
        return True
    if _LIST_LINE.match(before):
        return True
    # Markdown emphasis and quoting carry no sentence meaning here.
    bare = stripped.rstrip("*_`\"'“‘>#|[]( \t")
    if not bare:
        return True
    return bare.endswith((".", "!", "?", ":", ";"))


def _sentence_around(line: str, column: int) -> str:
    starts = [0]
    for match in re.finditer(r"[.!?:]\s+", line):
        starts.append(match.end())
    start = max(s for s in starts if s <= column)
    ends = [m.start() for m in re.finditer(r"[.!?]", line) if m.start() > column]
    end = min(ends) + 1 if ends else len(line)
    return line[start:end]


def _term_is_glossed(term: str, sentence: str) -> bool:
    """An appositive, a parenthetical, or a definition clause."""
    position = sentence.find(term)
    if position >= 0 and _APPOSITIVE_TAIL.match(sentence[position + len(term):]):
        return True
    for match in _PARENTHETICAL.finditer(sentence):
        inner = match.group(1).strip()
        if not inner:
            continue
        if term in inner and inner != term:
            return True
        # "CopyDesk (a prose gate)" glosses the term sitting before the bracket.
        prefix = sentence[: match.start()].rstrip()
        if prefix.endswith(term):
            return True
    if _DEFINITION.search(sentence):
        head = sentence.split(term, 1)
        if len(head) == 2 and _DEFINITION.search(head[1]):
            return True
    return False


def _vocabulary(preset: dict) -> frozenset:
    entry = (preset.get("rules") or {}).get("unglossed-term") or {}
    tokens = (entry.get("vocabulary") or {}).get("add", ()) or entry.get("add", ())
    return frozenset(tokens)


def _unglossed_findings(text: str, preset: dict, severity: str):
    """Flag a term's first use when it carries no gloss.

    Full detection needs named-entity recognition, which is far beyond a regex
    linter. The workable version is a user-maintained vocabulary: flag a
    capitalised token that is not sentence-initial, is absent from the merged
    vocabulary, and appears first with no gloss in its sentence.
    """
    if severity == "off":
        return ()
    vocabulary = _vocabulary(preset)
    seen: set = set()
    findings: list[Finding] = []
    base = 0
    for index, line in enumerate(text.split("\n"), start=1):
        for match in _TERM_CANDIDATE.finditer(line):
            term = match.group(0)
            if term in seen or term in _MASKING_SENTINELS or term in vocabulary:
                continue
            # A sentence-initial appearance still counts as the first one. Marking
            # it seen after the check would flag the term's next mid-sentence use,
            # which is a term the reader has already met.
            seen.add(term)
            if _term_is_sentence_initial(line, match.start()):
                continue
            if _term_is_glossed(term, _sentence_around(line, match.start())):
                continue
            findings.append(
                Finding(
                    index,
                    "unglossed-term",
                    f"{term} — first use carries no gloss",
                    severity,
                    span_start=base + match.start(),
                    span_end=base + match.end(),
                )
            )
        base += len(line) + 1
    return findings


def _pattern_findings(text: str, patterns: Optional[tuple[RulePattern, ...]] = None) -> Iterable[Finding]:
    findings: list[Finding] = []
    for pattern in (RULE_PATTERNS if patterns is None else patterns):
        for match in pattern.regex.finditer(text):
            findings.append(
                Finding(
                    _line_number(text, match.start()),
                    pattern.check,
                    _line_excerpt(text, match.start()),
                    pattern.severity,
                    span_start=match.start(),
                    span_end=match.end(),
                )
            )
    return findings


def _nested_table_findings(text: str) -> Iterable[Finding]:
    # Tables are excluded from prose checks, but this check needs to see their
    # indentation. All other exclusions stay active. Masking preserves offsets,
    # so positions here match the masked body every other span uses.
    visible = exclude_markdown(text, exclude_tables=False)
    lines = visible.split("\n")
    findings: list[Finding] = []
    base = 0
    for index, line in enumerate(lines):
        if re.match(r"^[ \t]+\|", line):
            for preceding in reversed(lines[:index]):
                if not preceding.strip():
                    break
                if _LIST_LINE.match(preceding):
                    findings.append(
                        Finding(
                            index + 1,
                            "nested-table",
                            _excerpt(line),
                            "error",
                            span_start=base,
                            span_end=base + len(line),
                        )
                    )
                    break
        base += len(line) + 1
    return findings


def lint(
    text: str,
    path: Optional[Union[str, Path]] = None,
    *,
    subject_is_own_unit: bool = False,
    channel: Optional[str] = None,
) -> list[Finding]:
    """Return deterministic checks for one Markdown document.

    The function is deliberately dependency-free so the CLI, hook, and future
    measurement scripts can import exactly the same exclusions and rules.

    `channel` resolves the preset as if routing had named that channel; the
    chat gate passes "chat" so a reply is judged by the chat style's own
    thresholds.
    """
    preset, patterns = effective_preset(path, channel=channel)
    body = exclude_markdown(text)
    records = _sentence_records(body, subject_is_own_unit=subject_is_own_unit)

    warn_words = _rule_number(preset, "sentence-length", "max", LONG_SENTENCE_WARNING_WORDS)
    error_words = _rule_number(preset, "sentence-length", "hardMax", LONG_SENTENCE_ERROR_WORDS)
    max_sentences = int(_rule_number(preset, "paragraph-length", "maxSentences", PARAGRAPH_MAX_SENTENCES))
    avg_min = _rule_number(preset, "avg-sentence-length", "min", AVG_SENTENCE_MIN_WORDS)
    avg_max = _rule_number(preset, "avg-sentence-length", "max", AVG_SENTENCE_MAX_WORDS)
    max_rate = _rule_number(preset, "long-sentence-rate", "maxRate", LONG_SENTENCE_RATE)
    em_dash_max = _rule_number(preset, "em-dash-rate", "maxPerThousandWords", EM_DASH_RATE_DEFAULT)
    min_stdev = _rule_number(preset, "sentence-variation", "minStdev", MIN_SENTENCE_VARIATION)
    exemption_ratio = _rule_number(preset, "list-dominated", "exemptionRatio", LIST_EXEMPTION_RATIO)

    exempt = _document_is_exempt(path, body, exemption_ratio)
    findings: list[Finding] = []

    sentence_severity = _rule_severity(preset, "sentence-length", "warning")
    paragraph_severity = _rule_severity(preset, "paragraph-length", "error")
    rate_severity = _rule_severity(preset, "long-sentence-rate", "error")
    average_severity = _rule_severity(preset, "avg-sentence-length", "warning")
    variation_severity = _rule_severity(preset, "sentence-variation", "warning")

    list_severity = _rule_severity(preset, "list-dominated", "off")
    if list_severity != "off" and _list_ratio(body) > exemption_ratio:
        findings.append(
            Finding(1, "list-dominated", f"{_list_ratio(body):.0%} of lines are list items", list_severity)
        )

    if sentence_severity != "off":
        for sentence in records:
            if sentence.words > error_words:
                findings.append(
                    Finding(
                        sentence.line,
                        "sentence-length",
                        _excerpt(sentence.text),
                        "error",
                        span_start=sentence.start,
                        span_end=sentence.end,
                    )
                )
            elif sentence.words > warn_words:
                findings.append(
                    Finding(
                        sentence.line,
                        "sentence-length",
                        _excerpt(sentence.text),
                        sentence_severity,
                        span_start=sentence.start,
                        span_end=sentence.end,
                    )
                )

    if paragraph_severity != "off":
        findings.extend(_paragraph_findings(body, exempt=exempt, severity=paragraph_severity, max_sentences=max_sentences))

    if not exempt and len(records) >= FILE_STATISTICS_MIN_SENTENCES:
        long_sentences = [sentence for sentence in records if sentence.words > warn_words]
        if rate_severity != "off" and len(long_sentences) > len(records) * max_rate:
            thresh_str = int(warn_words) if warn_words.is_integer() else warn_words
            findings.append(
                Finding(
                    1,
                    "long-sentence-rate",
                    f"{len(long_sentences)}/{len(records)} qualifying sentences exceed {thresh_str} words",
                    rate_severity,
                )
            )

        # Em dashes are counted over the same masked prose the sentence checks
        # read, so fences, inline code and tables contribute neither dashes nor
        # words.
        em_dash_severity = _rule_severity(preset, "em-dash-rate", "warning")
        em_dash_words = len(body.split())
        if em_dash_severity != "off" and em_dash_words:
            em_dash_count = body.count("\u2014")
            em_dash_per_thousand = em_dash_count / em_dash_words * 1000
            if em_dash_per_thousand > em_dash_max:
                findings.append(
                    Finding(
                        1,
                        "em-dash-rate",
                        f"{em_dash_count} em dashes in {em_dash_words} words ({em_dash_per_thousand:.1f} per 1,000)",
                        em_dash_severity,
                    )
                )

        average = sum(sentence.words for sentence in records) / len(records)
        if average_severity != "off" and (average < avg_min or average > avg_max):
            findings.append(
                Finding(1, "avg-sentence-length", f"average sentence length is {average:.1f} words", average_severity)
            )

        variation = sqrt(sum((sentence.words - average) ** 2 for sentence in records) / len(records))
        if variation < min_stdev:
            if variation_severity != "off":
                findings.append(
                    Finding(1, "sentence-variation", f"sentence length variation is {variation:.1f} words", variation_severity)
                )

    findings.extend(_pattern_findings(body, patterns))
    findings.extend(_unglossed_findings(body, preset, _rule_severity(preset, "unglossed-term", "warning")))
    findings.extend(_nested_table_findings(text))
    return sorted(findings, key=lambda finding: (finding.line, finding.severity != "error", finding.check, finding.excerpt))


# long-sentence-rate is the one blocking rule computed over the whole document.
# It is a ratio with no text to point at, always reported at line 1, so origin
# filtering cannot place it. It blocks when it newly fires instead.
#
# paragraph-length was here too, and being here cost three defects: one old
# violation switched the rule off for a whole file, a violation the model had
# just written was reported as pre-existing, and the block message named an
# error it said needed no change. It carries its paragraph's span now and
# takes the ordinary overlap path.
DOCUMENT_SCOPED_BLOCKING_RULES = frozenset({
    "long-sentence-rate", "avg-sentence-length", "sentence-variation", "list-dominated",
    "em-dash-rate",
})


def blocking_findings_for_retry(findings: Iterable[Finding]) -> list[Finding]:
    """The error-severity findings the writer is responsible for.

    Issue 27 measured 205 of 265 Markdown files here as blocking an edit, and
    none of those refusals came from the text being written. Narrowing the
    decision to newly written text is what makes refusal usable rather than
    obstructive.
    """
    return [
        finding
        for finding in findings
        if finding.severity == "error"
        and finding.origin == "new"
        and finding.check not in DOCUMENT_SCOPED_BLOCKING_RULES
    ]


def has_blocking_findings(findings: Iterable[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


def has_ai_tell_finding(text: str, findings: Iterable[Finding]) -> bool:
    if not any(finding.check == "banned-word" and finding.severity == "error" for finding in findings):
        return False
    return any(pattern.phrase in AI_TELL_PHRASES and pattern.regex.search(text) for pattern in RULE_PATTERNS)


def _overlaps_changed(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """True when a half-open span overlaps a changed range or contains a join.

    A deletion in proposed space is stored as a zero-width point ``(p, p)``.
    Strict containment (``start < p < end``) charges a sentence the deletion
    joined without blaming a neighbour that only abuts the point.
    """
    for cs, ce in ranges:
        if cs == ce:
            if start < cs < end:
                return True
        elif max(start, cs) < min(end, ce):
            return True
    return False


class _TooLargeToCompare(Exception):
    """The document pair is past ``OUTER_DIFF_LINE_CAP``.

    Raised rather than returned so a caller cannot mistake the fallback for a
    real comparison. The caller knows the edit's own bounds and uses them.
    """

    def __init__(self, total: int) -> None:
        super().__init__(total)
        self.total = total


def _changed_char_ranges(existing: str, proposed: str) -> list[tuple[int, int]]:
    """Half-open proposed-space character ranges where the two documents differ.

    The comparison runs on line-level opcodes first and narrows each replaced
    block by characters, so untouched context inside a replacement costs
    nothing and identical text is never attributed to the edit.

    A deletion leaves no proposed text, so it is recorded as a zero-width join
    at the point where the remaining sides now meet. Replaced blocks larger
    than ``INNER_DIFF_CHAR_CAP`` keep the whole block rather than running the
    quadratic inner matcher, and a pair of documents longer than
    ``OUTER_DIFF_LINE_CAP`` lines together skips the line matcher the same way.
    """
    a_lines = existing.split("\n")
    b_lines = proposed.split("\n")
    b_starts = []
    total = 0
    for line in b_lines:
        b_starts.append(total)
        total += len(line) + 1
    ranges: list[tuple[int, int]] = []
    if len(a_lines) + len(b_lines) > OUTER_DIFF_LINE_CAP:
        # Too large to compare inside a hook's timeout. The caller supplies the
        # edit's own footprint instead: charging the whole document would
        # refuse every pre-existing error in the file, which is the symptom
        # this attribution exists to remove.
        raise _TooLargeToCompare(total)
    matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            point = b_starts[j1] if j1 < len(b_starts) else total
            ranges.append((point, point))
            continue
        lo = b_starts[j1]
        hi = b_starts[j2 - 1] + len(b_lines[j2 - 1])
        if tag == "insert":
            ranges.append((lo, hi))
            continue
        chunk_a = "\n".join(a_lines[i1:i2])
        chunk_b = "\n".join(b_lines[j1:j2])
        if len(chunk_a) + len(chunk_b) > INNER_DIFF_CHAR_CAP:
            ranges.append((lo, hi))
            continue
        inner = difflib.SequenceMatcher(None, chunk_a, chunk_b, autojunk=False)
        for inner_tag, _a1, _a2, bj1, bj2 in inner.get_opcodes():
            if inner_tag == "equal":
                continue
            if bj1 == bj2:
                ranges.append((lo + bj1, lo + bj1))
            else:
                ranges.append((lo + bj1, lo + bj2))
    return ranges


def _compute_edit_origins(
    findings: list[Finding],
    existing: str,
    old_string: str,
    reconstructed: str,
) -> list[Finding]:
    """Attribute each finding to the edit or to the pre-existing document.

    Issue 8: attribution used to mark every finding anchored on a line inside
    the replacement span as new. The span includes unchanged context, so a
    pre-existing sentence beside or underneath the edited text blocked the
    write. Attribution now overlaps each finding's character span with the
    ranges where proposed actually differs from existing.

    A sentence that starts outside the changed characters but is cut into by
    them belongs to the edit: the model wrote part of it, so the sentence as
    it now stands is partly authored prose and must pass the rules. Its
    findings are attributed at sentence granularity, which also charges a
    rule a rewrite carries over inside a sentence it reworded. A deletion
    that joins two sentences is a zero-width point the merged sentence
    contains; a change landing immediately after a sentence's final
    character leaves that sentence untouched.
    """

    def all_existing() -> list[Finding]:
        return [Finding(f.line, f.check, f.excerpt, f.severity, origin="existing") for f in findings]

    if not findings:
        return []
    if not existing or old_string not in existing:
        # The file moved between reads: unattributable, so nothing blocks.
        return all_existing()

    masked_existing = exclude_markdown(existing)
    masked_proposed = exclude_markdown(reconstructed)
    try:
        changed = _changed_char_ranges(masked_existing, masked_proposed)
    except _TooLargeToCompare:
        # The replaced region runs from where old_string sat to the end of
        # whatever replaced it. Length change gives the second bound without
        # a comparison, so a long document still charges only its own edit.
        start = masked_existing.find(exclude_markdown(old_string))
        if start < 0:
            return all_existing()
        grown = len(masked_proposed) - len(masked_existing)
        end = start + len(exclude_markdown(old_string)) + max(0, grown)
        changed = [(start, min(end, len(masked_proposed)))]
    return _attribute_origins(findings, masked_proposed, changed)


def _attribute_origins(
    findings: list[Finding],
    masked_proposed: str,
    changed: list[tuple[int, int]],
) -> list[Finding]:
    """Mark each finding ``new`` or ``existing`` against changed ranges.

    Shared by the write-time gate, whose ranges come from comparing the
    existing document against the proposed one, and the staged check, whose
    ranges come from git's diff hunks when the pair is too large to compare.
    Both attribute at the same sentence granularity: text is owned when its
    span overlaps a change or a sentence a change cut into.
    """
    if not findings:
        return []
    owned = list(changed)
    if changed:
        for record in _sentence_records(masked_proposed):
            if _overlaps_changed(record.start, record.end, changed):
                owned.append((record.start, record.end))
    result: list[Finding] = []
    for f in findings:
        if f.span_start is None or f.span_end is None:
            result.append(Finding(f.line, f.check, f.excerpt, f.severity, origin="existing"))
            continue
        # A range edit tests each part the rule measured, so rewording a list
        # item cannot own a paragraph it was never counted in. A deletion is a
        # zero-width point and tests the whole extent: the point falls between
        # parts by definition, and removing text there is what merges two
        # paragraphs into one that breaks the cap.
        tested = f.spans or ((f.span_start, f.span_end),)
        points = [(lo, hi) for lo, hi in owned if lo == hi]
        widths = [(lo, hi) for lo, hi in owned if lo != hi]
        is_new = any(_overlaps_changed(lo, hi, widths) for lo, hi in tested)
        if not is_new and points:
            is_new = _overlaps_changed(f.span_start, f.span_end, points)
        result.append(Finding(f.line, f.check, f.excerpt, f.severity, origin="new" if is_new else "existing"))
    return result


def _finding_rollups(findings: list[Finding]) -> dict[str, dict[str, int]]:
    """Return complete origin and rule counts, taken before the display cap.

    ``_serialize_findings`` keeps only the first ``MAX_STORED_FINDINGS`` entries,
    so the summariser reads these rollups instead of counting the stored list.

    The ``blocking_`` pair counts error-severity findings alone, because only an
    error blocks a write. A warning left untouched in the rest of the document
    must not make a block read as mixed, and must not be charged for rework.
    """
    origin_totals: dict[str, int] = {}
    rule_totals: dict[str, int] = {}
    blocking_origin_totals: dict[str, int] = {}
    blocking_rule_totals: dict[str, int] = {}
    for finding in findings:
        origin_totals[finding.origin] = origin_totals.get(finding.origin, 0) + 1
        rule_totals[finding.check] = rule_totals.get(finding.check, 0) + 1
        if finding.severity == "error":
            blocking_origin_totals[finding.origin] = blocking_origin_totals.get(finding.origin, 0) + 1
            blocking_rule_totals[finding.check] = blocking_rule_totals.get(finding.check, 0) + 1
    return {
        "origin_totals": origin_totals,
        "rule_totals": rule_totals,
        "blocking_origin_totals": blocking_origin_totals,
        "blocking_rule_totals": blocking_rule_totals,
    }


def _serialize_findings(findings: list[Finding]) -> list[dict[str, object]]:
    log_text = os.environ.get("COPYDESK_LOG_FLAGGED_TEXT") != "0"
    serialized: list[dict[str, object]] = []
    for f in findings[:MAX_STORED_FINDINGS]:
        entry: dict[str, object] = {
            "rule": f.check,
            "severity": f.severity,
            "line": f.line,
            "origin": f.origin,
        }
        if log_text:
            entry["flagged_text"] = f.excerpt
        serialized.append(entry)
    return serialized


def _state_directory() -> Path:
    # CopyDesk is harness-neutral, so the default state path is XDG rather than
    # any one harness's directory. COPYDESK_STATE_DIR still overrides both.
    configured = os.environ.get("COPYDESK_STATE_DIR")
    if configured:
        return Path(configured)
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "copydesk"


def _state_path(state_dir: Path, session_id: str) -> Path:
    safe_session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    return state_dir / f"{safe_session_id}.json"


def _sweep_state(state_dir: Path, now: float) -> None:
    for candidate in state_dir.glob("*.json"):
        if candidate.name == HOOK_REGISTRY_NAME:
            continue  # the hook registry is not session state
        try:
            if now - candidate.stat().st_mtime > STATE_TTL_SECONDS:
                candidate.unlink()
        except OSError:
            continue


def _read_state(path: Path, now: float) -> dict[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"files": {}}
    except (OSError, json.JSONDecodeError):
        return {"files": {}}

    files = decoded.get("files") if isinstance(decoded, dict) else None
    if not isinstance(files, dict):
        return {"files": {}}

    valid_files: dict[str, object] = {}
    for file_path, entry in files.items():
        if not isinstance(file_path, str) or not isinstance(entry, dict):
            continue
        updated_at = entry.get("updated_at")
        if isinstance(updated_at, (int, float)) and now - updated_at <= STATE_TTL_SECONDS:
            valid_files[file_path] = entry
    return {"files": valid_files}


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(state, temporary, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _is_unconfigured_test_context() -> bool:
    """Return True if running in a test context without an explicit state directory override."""
    if "COPYDESK_STATE_DIR" in os.environ:
        return False
    if "unittest" in sys.modules or "pytest" in sys.modules:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def _record_event(event: dict[str, object]) -> None:
    if os.environ.get("COPYDESK_LOG") == "0":
        return
    if _is_unconfigured_test_context():
        return
    try:
        state_dir = _state_directory()
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / "events.jsonl"
        lock_path = state_dir / ".events.lock"

        with open(lock_path, "a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if log_path.is_file() and log_path.stat().st_size >= ROTATION_SIZE_BYTES:
                    rot2 = state_dir / "events.2.jsonl"
                    rot1 = state_dir / "events.1.jsonl"
                    if rot1.is_file():
                        os.replace(rot1, rot2)
                    os.replace(log_path, rot1)

                line = json.dumps(event, separators=(",", ":"))
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError, TypeError):
        pass


def record_turn_event(session_id: Optional[str] = None) -> None:
    event: dict[str, object] = {
        "ts": round(time.time(), 1),
        "event": "turn",
    }
    if session_id:
        event["session_id"] = session_id
    _record_event(event)


class Proposed(NamedTuple):
    path: str
    text: str
    session_id: str
    action: str          # "warn" or "block"; "ignore" never returns a record
    channel: str


def _proposed_document(payload: object) -> Optional[Proposed]:
    """Return the path, proposed Markdown, and session id or fail open."""
    if not isinstance(payload, dict):
        return None

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id")
    if tool_name not in {"Write", "Edit"} or not isinstance(tool_input, dict):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return None

    resolved, _ = effective_preset(file_path)
    decision = channels.decide(file_path, resolved)
    if decision.action == "ignore" or decision.channel is None:
        return None

    if tool_name == "Write":
        content = tool_input.get("content")
        return Proposed(file_path, content, session_id, decision.action, decision.channel) if isinstance(content, str) else None

    old_string = tool_input.get("old_string")
    new_string = tool_input.get("new_string")
    replace_all = tool_input.get("replace_all")
    if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str) or not isinstance(replace_all, bool):
        return None

    try:
        existing = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return None
    if old_string not in existing:
        return None

    occurrences = -1 if replace_all else 1
    return Proposed(file_path, existing.replace(old_string, new_string, occurrences), session_id, decision.action, decision.channel)


def _retry_limit(resolved: dict) -> int:
    """1 to 5, defaulting to 3. Anything else is a config mistake, not a crash."""
    value = (resolved.get("gate") or {}).get("retries", RETRY_LIMIT)
    if isinstance(value, int) and 1 <= value <= 5:
        return value
    return RETRY_LIMIT


def _warning_for_retry(hashes: list[str], limit: int = RETRY_LIMIT) -> str:
    if len(set(hashes)) == 1:
        detail = f"same content submitted {limit} times (sha256={hashes[-1]})"
    elif len(set(hashes)) == limit:
        detail = f"{limit} different attempts still failing (sha256={', '.join(hashes)})"
    else:
        detail = f"{limit} attempts still failing (sha256={', '.join(hashes)})"
    return f"CopyDesk gate passed after {limit} failed attempts: {detail}. Run /humanizer before the next edit."


def _write_retry_warning(message: str) -> None:
    """Make the pass-through warning visible to Claude and the debug log."""
    print(message, file=sys.stderr)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": message,
                }
            }
        )
    )


def run_hook(raw_payload: str) -> int:
    """Run the PreToolUse policy with fail-open handling for hook infrastructure."""
    try:
        payload = json.loads(raw_payload)
        tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
        tool_input = payload.get("tool_input") if isinstance(payload, dict) else None

        proposed_record = _proposed_document(payload)
        if proposed_record is None:
            return 0

        file_path = proposed_record.path
        proposed = proposed_record.text
        session_id = proposed_record.session_id
        action = proposed_record.action
        channel = proposed_record.channel

        # Time strictly around lint() call alone
        t_start = time.time()
        findings = lint(proposed, path=file_path)
        t_end = time.time()
        duration_ms = round((t_end - t_start) * 1000, 1)

        if tool_name == "Write":
            findings_with_origin = [Finding(f.line, f.check, f.excerpt, f.severity, origin="new") for f in findings]
            payload_content = str(tool_input.get("content", "")) if isinstance(tool_input, dict) else ""
            payload_bytes = len(payload_content.encode("utf-8"))
            payload_words = len(payload_content.split())
        else:
            old_string = str(tool_input.get("old_string", "")) if isinstance(tool_input, dict) else ""
            new_string = str(tool_input.get("new_string", "")) if isinstance(tool_input, dict) else ""
            try:
                existing = Path(file_path).read_text(encoding="utf-8")
            except OSError:
                existing = ""
            findings_with_origin = _compute_edit_origins(findings, existing, old_string, proposed)
            payload_bytes = len(old_string.encode("utf-8")) + len(new_string.encode("utf-8"))
            payload_words = len(old_string.split()) + len(new_string.split())

        doc_bytes = len(proposed.encode("utf-8"))
        body_sentences = len(_sentence_records(exclude_markdown(proposed)))
        findings_total = len(findings)
        rollups = _finding_rollups(findings_with_origin)

        now = time.time()

        if action == "warn":
            for finding in findings:
                print(finding.render(), file=sys.stderr)
            _record_event({
                "ts": round(now, 1),
                "event": "lint",
                "surface": "gate",
                "tool": tool_name,
                "path": str(file_path),
                "decision": "warn",
                "streak": 0,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_bytes": payload_bytes,
                "payload_words": payload_words,
                "sentences": body_sentences,
                "findings_total": findings_total,
                "origin_totals": rollups["origin_totals"],
                "rule_totals": rollups["rule_totals"],
                "blocking_origin_totals": rollups["blocking_origin_totals"],
                "blocking_rule_totals": rollups["blocking_rule_totals"],
                "findings": _serialize_findings(findings_with_origin),
                "session_id": session_id,
            })
            return 0

        state_dir = _state_directory()
        state_dir.mkdir(parents=True, exist_ok=True)
        _sweep_state(state_dir, now)
        state_path = _state_path(state_dir, session_id)
        state = _read_state(state_path, now)
        files = state["files"]
        if not isinstance(files, dict):
            return 0

        # The block decision reads origins rather than the whole document. Write
        # is unaffected: every Write finding is already marked new above.
        blocking = blocking_findings_for_retry(findings_with_origin)

        # Document-scoped rules block when they newly fire: absent from
        # lint(existing) and present in lint(proposed). The extra lint runs
        # only when there is nothing else to block on, so the common path
        # keeps its measured 17 ms median.
        scoped = [
            f
            for f in findings_with_origin
            if f.check in DOCUMENT_SCOPED_BLOCKING_RULES and f.severity == "error"
        ]
        newly_fired: list[Finding] = []
        if scoped:
            # The extra lint runs only when a whole-document rule is present,
            # so the common path keeps its measured 17 ms median.
            if tool_name == "Write":
                newly_fired = scoped
            else:
                previous = lint(existing, path=file_path) if existing else []
                already = {f.check for f in previous if f.severity == "error"}
                newly_fired = [f for f in scoped if f.check not in already]
        # Always join the block, not only when nothing else blocked. Landing in
        # the pre-existing count told the model an error it had just written
        # needed no change, and rules/editorial.json ships two of these at
        # error severity.
        blocking = blocking + [f for f in newly_fired if f not in blocking]

        preexisting_errors = sum(
            1
            for finding in findings_with_origin
            if finding.severity == "error"
            and finding not in blocking
            and finding.check not in DOCUMENT_SCOPED_BLOCKING_RULES
        )

        if not blocking:
            # A pass that clears an earlier block is attempt N, not a first-pass
            # success. Zero is reserved for a file with no preceding block.
            cleared = files.get(file_path)
            cleared_streak = cleared.get("streak", 0) if isinstance(cleared, dict) else 0
            if not isinstance(cleared_streak, int) or cleared_streak < 1:
                pass_streak = 0
            else:
                pass_streak = cleared_streak + 1
            files.pop(file_path, None)
            _write_state(state_path, state)
            _record_event({
                "ts": round(now, 1),
                "event": "lint",
                "surface": "gate",
                "tool": tool_name,
                "path": str(file_path),
                "decision": "pass",
                "streak": pass_streak,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_bytes": payload_bytes,
                "payload_words": payload_words,
                "sentences": body_sentences,
                "findings_total": findings_total,
                "origin_totals": rollups["origin_totals"],
                "rule_totals": rollups["rule_totals"],
                "blocking_origin_totals": rollups["blocking_origin_totals"],
                "blocking_rule_totals": rollups["blocking_rule_totals"],
                "findings": _serialize_findings(findings_with_origin),
                "session_id": session_id,
            })
            return 0

        content_hash = hashlib.sha256(proposed.encode("utf-8")).hexdigest()
        previous = files.get(file_path)
        previous_hashes = previous.get("hashes", []) if isinstance(previous, dict) else []
        hashes = [value for value in previous_hashes if isinstance(value, str)][-2:] + [content_hash]
        streak = (previous.get("streak", 0) if isinstance(previous, dict) else 0) + 1

        resolved, _ = effective_preset(file_path)
        retry_limit = _retry_limit(resolved)

        if streak >= retry_limit:
            files.pop(file_path, None)
            _write_state(state_path, state)
            _write_retry_warning(_warning_for_retry(hashes, retry_limit))
            _record_event({
                "ts": round(now, 1),
                "event": "lint",
                "surface": "gate",
                "tool": tool_name,
                "path": str(file_path),
                "decision": "escape",
                "streak": streak,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_bytes": payload_bytes,
                "payload_words": payload_words,
                "sentences": body_sentences,
                "findings_total": findings_total,
                "origin_totals": rollups["origin_totals"],
                "rule_totals": rollups["rule_totals"],
                "blocking_origin_totals": rollups["blocking_origin_totals"],
                "blocking_rule_totals": rollups["blocking_rule_totals"],
                "findings": _serialize_findings(findings_with_origin),
                "session_id": session_id,
            })
            return 0

        files[file_path] = {
            "content_hash": content_hash,
            "hashes": hashes,
            "streak": streak,
            "updated_at": now,
        }
        _write_state(state_path, state)
        _record_event({
            "ts": round(now, 1),
            "event": "lint",
            "surface": "gate",
            "tool": tool_name,
            "path": str(file_path),
            "decision": "block",
            "streak": streak,
            "duration_ms": duration_ms,
            "bytes": doc_bytes,
            "payload_bytes": payload_bytes,
            "payload_words": payload_words,
            "sentences": body_sentences,
            "findings_total": findings_total,
            "origin_totals": rollups["origin_totals"],
            "rule_totals": rollups["rule_totals"],
            "blocking_origin_totals": rollups["blocking_origin_totals"],
            "blocking_rule_totals": rollups["blocking_rule_totals"],
            "findings": _serialize_findings(findings_with_origin),
            "session_id": session_id,
        })
        # Print only what caused the block. Asking the model to fix text it did
        # not write is what made refusal obstructive.
        for finding in blocking:
            print(finding.render(), file=sys.stderr)
        if preexisting_errors:
            plural = "" if preexisting_errors == 1 else "s"
            print(
                f"{preexisting_errors} pre-existing error{plural} in this file did not cause the block "
                "and need no change.",
                file=sys.stderr,
            )
        if has_ai_tell_finding(proposed, blocking):
            print("Run /humanizer for the AI-tell failures before retrying.", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        return 0


def _chat_state_path(session_id: str) -> Path:
    """Retry state beside the .closer file, keyed on the session alone."""
    safe_session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    return _state_directory() / "sessions" / f"{safe_session_id}.chat"


def _chat_gate_mode(resolved: dict) -> str:
    """Whether a chat finding refuses the turn or is only recorded.

    Defaults to `warn`. Claude Code appends a replacement reply and leaves the
    refused one on screen, so every refusal costs the reader a duplicate
    answer. Blocking is available and opted into.
    """
    value = ((resolved.get("channels") or {}).get("chat") or {}).get("gate", CHAT_GATE_DEFAULT)
    return value if value in ("warn", "block") else CHAT_GATE_DEFAULT


def _read_chat_state(path: Path, now: float) -> dict[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    updated_at = decoded.get("updated_at")
    if not isinstance(updated_at, (int, float)) or now - updated_at > STATE_TTL_SECONDS:
        return {}
    return decoded


def run_chat_hook(raw_payload: str) -> int:
    """Judge one finished reply for the chat Stop hook. Fails open.

    Every finding is new text — a chat reply has no prior version — so the
    edit-origin machinery is skipped and `blocking_findings_for_retry`
    already describes the blocking set. Document-scoped statistical rules
    drop out because a short reply must not be measured against whole-document
    minimums, and rate rules stay quiet under the same sentence floor inside
    lint().

    `channels.chat.gate` decides what a blocking finding does. `warn`, the
    default, records the event and exits 0 in silence. `block` keeps the
    retry streak, the escape at the retry limit, and exit 2.
    """
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            return 0
        session_id = payload.get("session_id")
        message = payload.get("last_assistant_message")
        if not isinstance(session_id, str) or not session_id:
            return 0
        if not isinstance(message, str) or not message:
            return 0

        t_start = time.time()
        findings = lint(message, channel="chat")
        duration_ms = round((time.time() - t_start) * 1000, 1)

        doc_bytes = len(message.encode("utf-8"))
        body_sentences = len(_sentence_records(exclude_markdown(message)))
        findings_total = len(findings)
        rollups = _finding_rollups(findings)
        now = time.time()

        def _event(decision: str, streak: int) -> dict[str, object]:
            return {
                "ts": round(now, 1),
                "event": "lint",
                "surface": "chat",
                "tool": None,
                "decision": decision,
                "streak": streak,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_words": len(message.split()),
                "sentences": body_sentences,
                "findings_total": findings_total,
                "origin_totals": rollups["origin_totals"],
                "rule_totals": rollups["rule_totals"],
                "blocking_origin_totals": rollups["blocking_origin_totals"],
                "blocking_rule_totals": rollups["blocking_rule_totals"],
                "findings": _serialize_findings(findings),
                "session_id": session_id,
            }

        blocking = blocking_findings_for_retry(findings)
        state_path = _chat_state_path(session_id)
        state = _read_chat_state(state_path, now)

        if not blocking:
            # A pass that clears an earlier block is attempt N, not a first-pass
            # success, matching the write gate's streak bookkeeping.
            previous_streak = state.get("streak", 0) if isinstance(state, dict) else 0
            if not isinstance(previous_streak, int) or previous_streak < 1:
                pass_streak = 0
            else:
                pass_streak = previous_streak + 1
            try:
                state_path.unlink()
            except OSError:
                pass
            has_warnings = any(finding.severity == "warning" for finding in findings)
            _record_event(_event("warn" if has_warnings else "pass", pass_streak))
            return 0

        resolved, _ = effective_preset(channel="chat")

        if _chat_gate_mode(resolved) != "block":
            # The default. Record what the reply broke and say nothing the
            # reader sees: no stderr, exit 0. A refusal here would leave the
            # refused answer on screen beside its replacement.
            _record_event(_event("warn", 0))
            return 0

        content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        previous_hashes = state.get("hashes", []) if isinstance(state, dict) else []
        if not isinstance(previous_hashes, list):
            previous_hashes = []
        hashes = [value for value in previous_hashes if isinstance(value, str)][-2:] + [content_hash]
        previous_streak_value = state.get("streak", 0) if isinstance(state, dict) else 0
        streak = previous_streak_value + 1 if isinstance(previous_streak_value, int) else 1

        retry_limit = _retry_limit(resolved)

        if payload.get("stop_hook_active") is True:
            # Claude Code is already continuing because a Stop hook asked it
            # to. The streak file is the only other bound, and it expires, so
            # refusing again is what leaves the loop unbounded when it is lost.
            try:
                state_path.unlink()
            except OSError:
                pass
            _record_event(_event("escape", streak))
            return 0

        if streak >= retry_limit:
            try:
                state_path.unlink()
            except OSError:
                pass
            print(
                f"CopyDesk chat gate passed after {retry_limit} failed attempts "
                f"(sha256={', '.join(hashes)}).",
                file=sys.stderr,
            )
            _record_event(_event("escape", streak))
            return 0

        state_dir = state_path.parent
        state_dir.mkdir(parents=True, exist_ok=True)
        _write_state(state_path, {"streak": streak, "hashes": hashes, "updated_at": now})
        _record_event(_event("block", streak))
        for finding in blocking:
            print(finding.render(), file=sys.stderr)
        if has_ai_tell_finding(message, blocking):
            print("Run /humanizer for the AI-tell failures before retrying.", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        return 0


def _parse_since(since: str, now: float) -> Optional[float]:
    since = since.strip()
    match = re.match(r"^(\d+)\s*d$", since, re.IGNORECASE)
    if match:
        days = int(match.group(1))
        return now - days * 86400

    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", since)
    if match:
        year, month, day = map(int, match.groups())
        try:
            dt = datetime.datetime(year, month, day, 0, 0, 0)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def read_events(
    state_dir: Optional[Path] = None,
    since: Optional[str] = None,
    now: Optional[float] = None,
) -> list[dict[str, object]]:
    if state_dir is None:
        state_dir = _state_directory()

    events: list[dict[str, object]] = []

    # Rotation renames every generation under an exclusive lock on .events.lock.
    # Reading all three under a shared lock keeps the snapshot whole: without it,
    # a rotation between two generations moves one out of the reader's path and
    # the run silently loses a full generation. A missing or unwritable state
    # directory degrades to an unlocked read rather than to no reading at all.
    lock_file = None
    try:
        lock_file = open(state_dir / ".events.lock", "a", encoding="utf-8")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
    except OSError:
        if lock_file is not None:
            lock_file.close()
            lock_file = None

    try:
        for name in ("events.2.jsonl", "events.1.jsonl", "events.jsonl"):
            p = state_dir / name
            if not p.is_file():
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict) and "ts" in record and "event" in record:
                                events.append(record)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
    finally:
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    events.sort(key=lambda e: float(e.get("ts", 0)))

    if since:
        current_time = now if now is not None else time.time()
        cutoff = _parse_since(since, current_time)
        if cutoff is None:
            # Falling back to every event would present an unfiltered report as
            # a windowed one, so an unreadable window is an error, not a default.
            raise ValueError(f"unrecognised --since value {since!r}: expected <N>d or YYYY-MM-DD")
        events = [e for e in events if float(e.get("ts", 0)) >= cutoff]

    return events


def _latest_summary_with_rate(directory: Path) -> Optional[Path]:
    """Newest summary file that actually carries a rate, or None.

    Validating per file rather than validating only the newest name keeps one
    malformed or older-schema summary from hiding a readable baseline behind
    it.
    """
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob("*-summary.json"), reverse=True):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "rate" in data:
            return candidate
    return None


def get_prevention_summary(
    results_dir: Optional[Path] = None,
    now: Optional[float] = None,
    cwd: Optional[Path] = None,
) -> Optional[dict[str, object]]:
    latest_file: Optional[Path] = None
    if results_dir is not None:
        latest_file = _latest_summary_with_rate(results_dir)
    else:
        # The npm allowlist ships no eval/ directory, so an installed CopyDesk
        # has no bundled results. A report run inside a CopyDesk checkout then
        # reads the checkout's own, usually newer, baseline instead of ending
        # at not measured. The checkout-layout guard stops an unrelated
        # project's eval directory from posing as this metric.
        bundle_root = Path(__file__).resolve().parents[1]
        latest_file = _latest_summary_with_rate(bundle_root / "eval" / "results")
        if latest_file is None:
            workdir = cwd if cwd is not None else Path.cwd()
            if (workdir / "lib" / "linter.py").is_file():
                latest_file = _latest_summary_with_rate(workdir / "eval" / "results")

    if latest_file is None:
        return None

    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "rate" in data:
            current_time = now if now is not None else time.time()
            date_str = str(data.get("date", ""))
            age_str = ""
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                try:
                    y, m, d = map(int, date_str.split("-"))
                    dt = datetime.datetime(y, m, d, 0, 0, 0)
                    days_ago = int((current_time - dt.timestamp()) / 86400)
                    if days_ago == 0:
                        age_str = " (today)"
                    elif days_ago == 1:
                        age_str = " (1 day ago)"
                    elif days_ago > 1:
                        age_str = f" ({days_ago} days ago)"
                except Exception:
                    pass
            return {
                "rate": float(data["rate"]),
                "date": date_str,
                "statistic": data.get("statistic", "median across sequences at final turn"),
                "source": data.get("source", "eval/results/baseline-results.md"),
                "age_str": age_str,
            }
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _counts_from_findings(event: dict[str, object], key: str, blocking_only: bool = False) -> dict[str, int]:
    """Count one finding field over the stored list, capped at MAX_STORED_FINDINGS."""
    counts: dict[str, int] = {}
    findings = event.get("findings", [])
    if not isinstance(findings, list):
        return counts
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if blocking_only and str(finding.get("severity", "")) != "error":
            continue
        value = str(finding.get(key, "new" if key == "origin" else ""))
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _event_counts(
    event: dict[str, object],
    rollup_key: str,
    finding_key: str,
    blocking_only: bool = False,
) -> dict[str, int]:
    """Prefer an event's complete rollup, falling back to its capped finding list.

    Events written before rollups existed carry only the capped list, so they
    stay readable at the accuracy they were recorded with.
    """
    rollup = event.get(rollup_key)
    if isinstance(rollup, dict):
        counts: dict[str, int] = {}
        for name, count in rollup.items():
            try:
                counts[str(name)] = int(count)
            except (TypeError, ValueError):
                continue
        return counts
    return _counts_from_findings(event, finding_key, blocking_only=blocking_only)


def _event_streak(event: dict[str, object]) -> int:
    """Return an event's attempt number, treating an unreadable value as zero."""
    try:
        return int(event.get("streak", 0))
    except (TypeError, ValueError):
        return 0


def _event_rule_totals(event: dict[str, object]) -> dict[str, int]:
    return _event_counts(event, "rule_totals", "rule")


def _event_blocking_rule_totals(event: dict[str, object]) -> dict[str, int]:
    return _event_counts(event, "blocking_rule_totals", "rule", blocking_only=True)


def _event_blocking_origin_totals(event: dict[str, object]) -> dict[str, int]:
    return _event_counts(event, "blocking_origin_totals", "origin", blocking_only=True)


def summarize_events(
    events: list[dict[str, object]],
    now: Optional[float] = None,
    prevention_dir: Optional[Path] = None,
) -> dict[str, object]:
    current_time = now if now is not None else time.time()
    lint_events = [e for e in events if e.get("event") == "lint"]
    turn_events = [e for e in events if e.get("event") == "turn"]
    # A CLI lint blocks no write and identifies no changed region, so it cannot
    # answer whether the gate helped. Gate effectiveness reads gate events only:
    # a pre-commit refusal stops a commit, not a write, so it is bucketed beside
    # the CLI's rather than counted as gate activity.
    gate_events = [e for e in lint_events if str(e.get("surface", "gate")) == "gate"]
    cli_events = [e for e in lint_events if str(e.get("surface", "gate")) == "cli"]
    precommit_events = [e for e in lint_events if str(e.get("surface", "gate")) == "pre-commit"]
    # A chat refusal stops a reply, not a write, so it reports beside the
    # pre-commit and CLI figures rather than inside the write-gate totals.
    chat_events = [e for e in lint_events if str(e.get("surface", "gate")) == "chat"]

    if events:
        min_ts = min(float(e.get("ts", current_time)) for e in events)
        max_ts = max(float(e.get("ts", current_time)) for e in events)
    else:
        min_ts = current_time
        max_ts = current_time

    start_day = datetime.date.fromtimestamp(min_ts)
    end_day = datetime.date.fromtimestamp(max_ts)
    start_date = start_day.strftime("%Y-%m-%d")
    end_date = end_day.strftime("%Y-%m-%d")
    # Count the calendar days the report prints, inclusive of both ends.
    # Rounding an elapsed-seconds span reported a single day as two whenever
    # the events covered more than twelve hours.
    window_days = ((end_day - start_day).days + 1) if events else 0

    # Work counts first attempts alone: a pass at streak 0, a block at streak 1.
    # A retry re-sends a document the gate already saw, so counting it as a new
    # write would lower the block rate every time a block gets resolved. Retries
    # are priced in the rework figures instead.
    initial_events = [e for e in gate_events if _event_streak(e) <= 1]

    total_writes = len(initial_events)
    # A warn is a pass: the write went through, with warnings printed beside it.
    # Counting it as neither a pass nor a block left the rows short of the total.
    passed_first = sum(
        1 for e in initial_events if e.get("decision") in ("pass", "warn") and _event_streak(e) == 0
    )
    passed_first_with_warnings = sum(
        1 for e in initial_events if e.get("decision") == "warn" and _event_streak(e) == 0
    )
    blocked = sum(1 for e in initial_events if e.get("decision") == "block")
    escaped = sum(1 for e in gate_events if e.get("decision") == "escape")

    passed_first_rate = (passed_first / total_writes * 100) if total_writes else 0.0
    blocked_rate = (blocked / total_writes * 100) if total_writes else 0.0
    escaped_rate = (escaped / total_writes * 100) if total_writes else 0.0

    new_only_blocks = 0
    mixed_blocks = 0
    existing_only_blocks = 0

    rework_by_rule_counts: dict[str, int] = {}
    rework_by_rule_words: dict[str, int] = {}
    rework_by_rule_false: dict[str, int] = {}
    top_rules_counts: dict[str, int] = {}

    file_block_counts: dict[str, int] = {}
    file_existing_counts: dict[str, int] = {}

    rework_rewrites = 0
    rework_words = 0
    session_file_rework: dict[tuple[str, str], list[int]] = {}
    # Rules that blocked the previous attempt on a file, per session and path.
    # The next attempt's payload is the rework those rules charged for.
    pending_block_rules: dict[tuple[str, str], list[str]] = {}

    for e in sorted(gate_events, key=lambda ev: float(ev.get("ts", 0))):
        decision = e.get("decision")
        streak = _event_streak(e)
        p_words = int(e.get("payload_words", 0))
        file_path = str(e.get("path", ""))
        session_id = str(e.get("session_id", ""))
        rule_totals = _event_rule_totals(e)
        # Origin and rework read the blocking subset. A warning changes nothing
        # about whether the write went through, so it cannot make a block
        # unresolvable and cannot charge its rule for the retry.
        blocking_rule_totals = _event_blocking_rule_totals(e)
        blocking_origin_totals = _event_blocking_origin_totals(e)
        key = (session_id, file_path)

        for rule, count in rule_totals.items():
            top_rules_counts[rule] = top_rules_counts.get(rule, 0) + count

        # A first attempt ends any earlier cycle for this file without paying it.
        blocking_rules = pending_block_rules.pop(key, [])

        if streak > 1:
            rework_rewrites += 1
            rework_words += p_words
            if key not in session_file_rework:
                session_file_rework[key] = []
            session_file_rework[key].append(p_words)
            # Charge this retry's words to the rules that forced it, not to the
            # first attempt, which the model would have sent anyway.
            for rule in blocking_rules:
                rework_by_rule_words[rule] = rework_by_rule_words.get(rule, 0) + p_words

        if decision == "block":
            pending_block_rules[key] = list(blocking_rule_totals)
            new_count = blocking_origin_totals.get("new", 0)
            existing_count = blocking_origin_totals.get("existing", 0)
            is_false = bool(existing_count)

            # The origin split and the per-file counts read first blocks alone,
            # so they keep summing to the block total above. The per-rule
            # columns below count every firing, retries included, because they
            # measure how often a rule fires rather than how often work began.
            if streak <= 1:
                file_block_counts[file_path] = file_block_counts.get(file_path, 0) + 1
                if not existing_count:
                    new_only_blocks += 1
                elif not new_count:
                    existing_only_blocks += 1
                    file_existing_counts[file_path] = file_existing_counts.get(file_path, 0) + 1
                else:
                    mixed_blocks += 1

            for r in blocking_rule_totals:
                rework_by_rule_counts[r] = rework_by_rule_counts.get(r, 0) + 1
                rework_by_rule_words.setdefault(r, 0)
                if is_false:
                    rework_by_rule_false[r] = rework_by_rule_false.get(r, 0) + 1

    unresolvable_blocks = mixed_blocks + existing_only_blocks
    unresolvable_rate = (unresolvable_blocks / blocked * 100) if blocked else 0.0

    largest_rework = None
    if session_file_rework:
        max_key = max(session_file_rework.keys(), key=lambda k: sum(session_file_rework[k]))
        words_list = session_file_rework[max_key]
        largest_rework = {
            "path": max_key[1],
            "words_per_attempt": words_list[0] if words_list else 0,
            "attempts": len(words_list),
            "total_words": sum(words_list),
        }

    durations = [float(e["duration_ms"]) for e in lint_events if "duration_ms" in e]
    if durations:
        sorted_durations = sorted(durations)
        n = len(sorted_durations)
        p50 = sorted_durations[int(n * 0.50)]
        p95 = sorted_durations[min(n - 1, int(n * 0.95))]
        total_time_s = sum(durations) / 1000.0
    else:
        p50, p95, total_time_s = 0.0, 0.0, 0.0

    # Weekly rates use the same basis as the headline block rate, so a week of
    # heavy retrying does not read as a week of falling blocks.
    weekly_events: dict[str, list[dict[str, object]]] = {}
    for e in initial_events:
        ts = float(e.get("ts", 0))
        dt = datetime.datetime.fromtimestamp(ts)
        monday = dt - datetime.timedelta(days=dt.weekday())
        week_label = monday.strftime("%b %d")
        if week_label not in weekly_events:
            weekly_events[week_label] = []
        weekly_events[week_label].append(e)

    weekly_rates: list[dict[str, object]] = []
    for label, w_events in weekly_events.items():
        w_total = len(w_events)
        w_blocked = sum(1 for we in w_events if we.get("decision") == "block")
        w_rate = (w_blocked / w_total * 100) if w_total else 0.0
        weekly_rates.append({
            "label": label,
            "total": w_total,
            "blocked": w_blocked,
            "rate": w_rate,
        })

    rework_by_rule: list[dict[str, object]] = []
    for r in sorted(rework_by_rule_counts.keys(), key=lambda k: (rework_by_rule_words.get(k, 0), rework_by_rule_counts.get(k, 0)), reverse=True):
        rework_by_rule.append({
            "rule": r,
            "blocks": rework_by_rule_counts[r],
            "words_resent": rework_by_rule_words.get(r, 0),
            "false_blocks": rework_by_rule_false.get(r, 0),
        })

    most_blocked_files: list[dict[str, object]] = []
    for p, b_count in sorted(file_block_counts.items(), key=lambda item: item[1], reverse=True):
        if b_count > 0:
            most_blocked_files.append({
                "path": p,
                "blocks": b_count,
                "existing_only": file_existing_counts.get(p, 0),
            })

    events_missing_flagged_text = 0
    for e in lint_events:
        findings = e.get("findings", [])
        if isinstance(findings, list) and findings:
            if not all("flagged_text" in f for f in findings if isinstance(f, dict)):
                events_missing_flagged_text += 1

    cli_blocked = sum(1 for e in cli_events if e.get("decision") == "block")
    cli_words = sum(int(e.get("payload_words", 0)) for e in cli_events)

    prevention = get_prevention_summary(prevention_dir, now=current_time)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": window_days,
        "total_events": len(events),
        "lint_events_count": len(lint_events),
        "gate_events_count": len(gate_events),
        "turn_events_count": len(turn_events),
        "work": {
            "total_writes": total_writes,
            "passed_first": passed_first,
            "passed_first_rate": passed_first_rate,
            "passed_first_with_warnings": passed_first_with_warnings,
            "blocked": blocked,
            "blocked_rate": blocked_rate,
            "escaped": escaped,
            "escaped_rate": escaped_rate,
        },
        "blocks_by_origin": {
            "new_only": new_only_blocks,
            "mixed": mixed_blocks,
            "existing_only": existing_only_blocks,
            "unresolvable_count": unresolvable_blocks,
            "unresolvable_rate": unresolvable_rate,
        },
        "time": {
            "p50_ms": p50,
            "p95_ms": p95,
            "total_s": total_time_s,
            "runs": len(lint_events),
        },
        "cli": {
            "lints": len(cli_events),
            "blocked": cli_blocked,
            "words": cli_words,
        },
        "precommit": {
            "lints": len(precommit_events),
            "blocked": sum(1 for e in precommit_events if e.get("decision") == "block"),
            "words": sum(int(e.get("payload_words", 0)) for e in precommit_events),
        },
        "chat": {
            "replies": sum(1 for e in chat_events if _event_streak(e) <= 1),
            "refused": sum(1 for e in chat_events if e.get("decision") == "block" and _event_streak(e) <= 1),
            "retries": sum(1 for e in chat_events if _event_streak(e) > 1),
            "escaped": sum(1 for e in chat_events if e.get("decision") == "escape"),
            "words": sum(int(e.get("payload_words", 0)) for e in chat_events),
        },
        "cost": {
            "reminder_turns": len(turn_events),
            "reminder_word_count": REMINDER_WORD_COUNT,
            "reminder_words": len(turn_events) * REMINDER_WORD_COUNT,
            "reminder_tokens_est": round(len(turn_events) * REMINDER_WORD_COUNT * 4 / 3),
            "rework_rewrites": rework_rewrites,
            "rework_words": rework_words,
            "rework_tokens_est": round(rework_words * 4 / 3),
            "largest_rework": largest_rework,
        },
        "rework_by_rule": rework_by_rule,
        "top_rules": [{"rule": r, "count": c} for r, c in sorted(top_rules_counts.items(), key=lambda i: i[1], reverse=True)],
        "most_blocked_files": most_blocked_files,
        "weekly_rates": weekly_rates,
        "prevention": prevention,
        "flagged_text_opt_out": {
            "active": events_missing_flagged_text > 0,
            "missing_events": events_missing_flagged_text,
            "total_events": len(lint_events),
        },
    }


def _format_token_k(tokens: int) -> str:
    if tokens >= 1000:
        return f"~{tokens // 1000}k"
    return f"~{tokens}"


def _display_path(path: Path) -> str:
    """Collapse a path under the home directory to ``~/…`` for readability."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _format_bar(rate: float) -> str:
    count = int(round(rate * 0.85))
    return "#" * count


def _day_count(days: object) -> str:
    """Render a window length with the right singular or plural noun."""
    count = int(days)  # type: ignore[arg-type]
    return f"{count} day" if count == 1 else f"{count} days"


def format_stats_terminal(summary: dict[str, object]) -> str:
    lines: list[str] = []
    start_date = summary["start_date"]
    end_date = summary["end_date"]
    days = summary["days"]

    lines.append(f"CopyDesk — {start_date} to {end_date} ({_day_count(days)})")
    lines.append("")

    work = summary["work"]
    assert isinstance(work, dict)
    lines.append("Work")
    lines.append(f"  Markdown writes seen          {work['total_writes']:>3}   gate first attempts; retries and CLI lints counted separately")
    warned = int(work.get("passed_first_with_warnings", 0))
    passed_note = f"   {work['passed_first_rate']:>5.1f}%"
    if warned > 0:
        passed_note += f"   of which {warned} passed with warnings"
    lines.append(f"  Passed first time             {work['passed_first']:>3}{passed_note}")
    lines.append(f"  Blocked                        {work['blocked']:>3}   {work['blocked_rate']:>5.1f}%")
    lines.append(f"  Escaped after 3 attempts        {work['escaped']:>3}")
    lines.append("")

    origin = summary["blocks_by_origin"]
    assert isinstance(origin, dict)
    lines.append("Blocks by origin")
    lines.append(f"  new text only                  {origin['new_only']:>3}   findings in prose the model wrote")
    lines.append(f"  mixed                           {origin['mixed']:>3}   passable only by editing untouched text")
    lines.append(f"  existing text only              {origin['existing_only']:>3}   the edit introduced no findings")
    if int(origin["unresolvable_count"]) > 0 and int(work["blocked"]) > 0:
        lines.append("")
        lines.append(f"  {origin['unresolvable_count']} of {work['blocked']} blocks ({origin['unresolvable_rate']:.1f}%) could not be resolved within the edited region.")
    lines.append("")

    timing = summary["time"]
    assert isinstance(timing, dict)
    lines.append("Time")
    lines.append(f"  p50 {timing['p50_ms']:.0f}ms    p95 {timing['p95_ms']:.0f}ms    total {timing['total_s']:.1f}s across {timing['runs']} runs")
    lines.append("")

    cost = summary["cost"]
    assert isinstance(cost, dict)
    lines.append("Cost")
    lines.append(f"  Reminder       {cost['reminder_turns']:>5,} turns     {cost['reminder_words']:>6,} words re-sent   {_format_token_k(int(cost['reminder_tokens_est']))} input tokens")
    lines.append(f"  Rework            {cost['rework_rewrites']:>2} rewrites  {cost['rework_words']:>6,} words re-sent    {_format_token_k(int(cost['rework_tokens_est']))} output tokens")
    largest = cost.get("largest_rework")
    if isinstance(largest, dict):
        lines.append(f"  Largest rework    {largest['path']}   {largest['words_per_attempt']:,} words x {largest['attempts']} attempts")
    lines.append("")
    lines.append("  Word counts are measured. Token figures are estimated at 4 characters each.")
    lines.append("")

    cli = summary.get("cli")
    if isinstance(cli, dict) and int(cli.get("lints", 0)) > 0:
        lines.append("CLI lints (not gate activity)")
        lines.append(f"  {cli['lints']} runs, {cli['blocked']} with blocking findings, {int(cli['words']):,} words linted")
        lines.append("  Excluded from the work, origin and rework figures above.")
        lines.append("")

    precommit = summary.get("precommit")
    if isinstance(precommit, dict) and int(precommit.get("lints", 0)) > 0:
        lines.append("Pre-commit lints (not gate activity)")
        lines.append(f"  {precommit['lints']} runs, {precommit['blocked']} refusing the commit, {int(precommit['words']):,} words checked")
        lines.append("  Excluded from the work, origin and rework figures above.")
        lines.append("")

    chat = summary.get("chat")
    if isinstance(chat, dict) and int(chat.get("replies", 0)) > 0:
        lines.append("Chat replies (not gate activity)")
        lines.append(f"  {chat['replies']} replies, {chat['refused']} refused, {int(chat['words']):,} words judged")
        lines.append(f"  {chat['retries']} rewrites, {chat['escaped']} passed through after the retry limit")
        lines.append("  Excluded from the work, origin and rework figures above.")
        lines.append("")

    rework_by_rule = summary.get("rework_by_rule", [])
    if isinstance(rework_by_rule, list) and rework_by_rule:
        lines.append("Rework by rule")
        lines.append("  RULE                 BLOCKS   WORDS RESENT   FALSE")
        for row in rework_by_rule:
            if isinstance(row, dict):
                lines.append(f"  {str(row['rule']):<20} {int(row['blocks']):>6}   {int(row['words_resent']):>12,}   {int(row['false_blocks']):>5}")
        lines.append("")
    else:
        top_rules = summary.get("top_rules", [])
        if isinstance(top_rules, list) and top_rules:
            lines.append("Top rules")
            for row in top_rules[:5]:
                if isinstance(row, dict):
                    lines.append(f"  {str(row['rule']):<20} {int(row['count']):>6}")
            lines.append("")

    most_blocked = summary.get("most_blocked_files", [])
    if isinstance(most_blocked, list) and len(most_blocked) > 1:
        lines.append("Most blocked files")
        for row in most_blocked[:5]:
            if isinstance(row, dict):
                lines.append(f"  {str(row['path']):<35} {row['blocks']:>2} blocks, {row['existing_only']:>2} existing-only")
        lines.append("")

    weekly_rates = summary.get("weekly_rates", [])
    if isinstance(weekly_rates, list) and weekly_rates:
        lines.append("Block rate by week")
        if len(weekly_rates) == 1:
            w = weekly_rates[0]
            lines.append(f"  {w['label']}  {_format_bar(float(w['rate'])):<24} {float(w['rate']):>5.1f}%")
            lines.append("          (one partial week; no trend yet)")
        else:
            for w in weekly_rates:
                lines.append(f"  {w['label']}  {_format_bar(float(w['rate'])):<24} {float(w['rate']):>5.1f}%")
        lines.append("")

    prevention = summary.get("prevention")
    lines.append("Prevention")
    if isinstance(prevention, dict):
        lines.append(f"  {prevention['rate']:.2f} findings per 1,000 words of chat")
        lines.append(f"  from {prevention['source']}, measured {prevention['date']}{prevention.get('age_str', '')}")
    else:
        lines.append("  not measured — no corpus results under eval/results/")
        lines.append("  run eval/run-corpus.sh, or copydesk baseline for a free estimate")

    opt_out = summary.get("flagged_text_opt_out")
    if isinstance(opt_out, dict) and opt_out.get("active"):
        lines.append("")
        lines.append(f"Note: COPYDESK_LOG_FLAGGED_TEXT=0 was set for {opt_out['missing_events']} of {opt_out['total_events']} events.")
        lines.append("Rule counts are complete. Per-finding text is unavailable for that period.")

    return "\n".join(lines)


def format_report_markdown(summary: dict[str, object], source: Optional[Path] = None) -> str:
    # The report is the durable record, so its source line names the log the run
    # actually read. COPYDESK_STATE_DIR moves that log, and a fixed default
    # would credit the numbers to a file this run never opened.
    if source is None:
        source = _state_directory() / "events.jsonl"
    lines: list[str] = []
    report_date = summary["end_date"]
    start_date = summary["start_date"]
    end_date = summary["end_date"]
    days = summary["days"]
    lint_count = summary["lint_events_count"]
    turn_count = summary["turn_events_count"]

    lines.append(f"# CopyDesk telemetry — {report_date}")
    lines.append("")
    lines.append(f"Window: {start_date} to {end_date} ({_day_count(days)})")
    lines.append(f"Source: {_display_path(Path(source))} ({lint_count} lint events, {turn_count} turn events)")
    lines.append("")

    work = summary["work"]
    assert isinstance(work, dict)
    lines.append("## Work")
    lines.append("")
    lines.append("Gate events only, counting first attempts. A retry is rework rather than a new write. A CLI lint blocks no write, so it is reported separately.")
    lines.append("")
    lines.append("| Measure | Count | Rate |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Markdown writes seen | {work['total_writes']} | |")
    lines.append(f"| Passed first time | {work['passed_first']} | {work['passed_first_rate']:.1f}% |")
    lines.append(f"| Blocked | {work['blocked']} | {work['blocked_rate']:.1f}% |")
    lines.append(f"| Escaped after 3 attempts | {work['escaped']} | {work['escaped_rate']:.1f}% |")
    lines.append("")
    warned = int(work.get("passed_first_with_warnings", 0))
    if warned > 0:
        lines.append(f"Of the first-time passes, {warned} passed with warnings printed beside the write.")
        lines.append("")

    origin = summary["blocks_by_origin"]
    assert isinstance(origin, dict)
    lines.append("## Blocks by origin")
    lines.append("")
    lines.append("| Origin | Blocks | Resolvable in scope |")
    lines.append("|---|---:|---|")
    lines.append(f"| new text only | {origin['new_only']} | yes |")
    lines.append(f"| mixed | {origin['mixed']} | no |")
    lines.append(f"| existing text only | {origin['existing_only']} | no |")
    lines.append("")

    timing = summary["time"]
    assert isinstance(timing, dict)
    lines.append("## Time")
    lines.append("")
    lines.append(f"p50 {timing['p50_ms']:.0f}ms    p95 {timing['p95_ms']:.0f}ms    total {timing['total_s']:.1f}s across {timing['runs']} runs")
    lines.append("")

    cost = summary["cost"]
    assert isinstance(cost, dict)
    lines.append("## Cost")
    lines.append("")
    lines.append(f"Reminder injection is exact: {cost['reminder_turns']:,} turns at {cost['reminder_word_count']} words each.")
    lines.append("Re-authoring is estimated from document size and is marked as an estimate.")
    lines.append("")
    lines.append("| Measure | Count | Words | Tokens |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Reminder | {cost['reminder_turns']:,} turns | {cost['reminder_words']:,} words | {_format_token_k(int(cost['reminder_tokens_est']))} input tokens |")
    lines.append(f"| Rework | {cost['rework_rewrites']} rewrites | {cost['rework_words']:,} words | {_format_token_k(int(cost['rework_tokens_est']))} output tokens |")
    lines.append("")

    cli = summary.get("cli")
    if isinstance(cli, dict) and int(cli.get("lints", 0)) > 0:
        lines.append("## CLI lints")
        lines.append("")
        lines.append(f"{cli['lints']} runs, {cli['blocked']} with blocking findings, {int(cli['words']):,} words linted.")
        lines.append("A CLI run blocks no write and identifies no changed region, so it is excluded from the figures above.")
        lines.append("")

    precommit = summary.get("precommit")
    if isinstance(precommit, dict) and int(precommit.get("lints", 0)) > 0:
        lines.append("## Pre-commit lints")
        lines.append("")
        lines.append(f"{precommit['lints']} runs, {precommit['blocked']} refusing the commit, {int(precommit['words']):,} words checked.")
        lines.append("A pre-commit refusal stops a commit, not a write, so it is excluded from the figures above.")
        lines.append("")

    chat = summary.get("chat")
    if isinstance(chat, dict) and int(chat.get("replies", 0)) > 0:
        lines.append("## Chat replies")
        lines.append("")
        lines.append(f"{chat['replies']} replies, {chat['refused']} refused, {int(chat['words']):,} words judged.")
        lines.append(f"{chat['retries']} rewrites, {chat['escaped']} passed through after the retry limit.")
        lines.append("A chat refusal stops a reply, not a write, so it is excluded from the figures above.")
        lines.append("")

    rework_by_rule = summary.get("rework_by_rule", [])
    if isinstance(rework_by_rule, list) and rework_by_rule:
        lines.append("## Rework by rule")
        lines.append("")
        lines.append("| Rule | Blocks | Words resent | False blocks |")
        lines.append("|---|---:|---:|---:|")
        for row in rework_by_rule:
            if isinstance(row, dict):
                lines.append(f"| {row['rule']} | {row['blocks']} | {row['words_resent']:,} | {row['false_blocks']} |")
        lines.append("")

    prevention = summary.get("prevention")
    lines.append("## Prevention")
    lines.append("")
    if isinstance(prevention, dict):
        lines.append(f"{prevention['rate']:.2f} findings per 1,000 words of chat")
        lines.append(f"from {prevention['source']}, measured {prevention['date']}{prevention.get('age_str', '')}")
    else:
        lines.append("not measured — no corpus results under eval/results/")
    lines.append("")

    return "\n".join(lines)


def user_layer() -> dict:
    """Built-ins, then styles, then the user file. No project or local layer.

    Static files render from these three only. Project and local settings
    ride the reminder's delta line instead, so two repositories never fight
    over one global file.
    """
    if config is None:
        return {}
    return config.resolve(_rules_dir(), None, user_path=config.user_config_path(),
                          project_path=None, local_path=None, channel="chat")


def stale_output_styles(home: Path) -> list[str]:
    """The installed output styles that no longer match their inputs.

    The comparison re-renders what setup would write today and compares
    bytes. That catches both kinds of drift: an input changed since the
    install, and an installed file edited by hand — its build stamp
    survives untouched, so comparing stamps alone called every hand-edited
    file fresh.

    SETUP_WRITER is the only writer files under ~/.claude/output-styles
    can carry — shipped copies never install there — so re-rendering
    through it reproduces what setup wrote. The reminder and doctor both
    call this, so neither can hash a different payload from the wizard.

    Known names are enumerated rather than globbed. The current install is
    copydesk.md, whose stem has no hyphen, so a `copydesk-*.md` glob stops
    matching it; the retired per-level files must stay visible too, until
    setup migrates them away.
    """
    directory = home / ".claude" / "output-styles"
    if instructions is None or not directory.is_dir():
        return []
    try:
        layer = user_layer()
        fresh = instructions.render_output_style(
            layer, writer=instructions.SETUP_WRITER
        )
    except Exception:
        return []         # cannot re-render: fail open, say nothing
    stale: list[str] = []
    names = ["copydesk.md", *(
        f"copydesk-{level}.md" for level in instructions.VERBOSITY_LEVELS
    )]
    for name in names:
        installed = directory / name
        if not installed.is_file():
            continue
        try:
            rendered = installed.read_text(encoding="utf-8")
        except OSError:
            continue      # unreadable: say nothing about it
        if rendered != fresh:
            stale.append(name)
    return stale


def _fingerprint_notice(home: Path) -> Optional[str]:
    """One line when an installed static file no longer matches its inputs."""
    stale = stale_output_styles(home)
    if not stale:
        return None
    return f"{stale[0]} is out of date. Run: copydesk setup --repair"


_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+\S")


def _closing_block(text: str) -> Optional[str]:
    """The final contiguous run of list items, or None.

    Trailing blank lines are skipped, and an indented continuation stays with
    the item it wraps. Anything else ends the run, so body bullets and closing
    prose stay out of the hash.
    """
    lines = text.rstrip().splitlines()
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    start = end
    while start and (_LIST_ITEM.match(lines[start - 1]) or
                     (lines[start - 1].startswith((" ", "\t")) and lines[start - 1].strip())):
        start -= 1
    if start == end or not _LIST_ITEM.match(lines[start]):
        return None
    return "\n".join(line.strip() for line in lines[start:end])


def _closer_hash(text: str) -> Optional[str]:
    """Hash the reply's closing list, or None when it has none.

    A restated decisions list is the most reported chat failure, and the
    reminder hook is the one place a chat-side check can run, because chat
    never reaches the gate.
    """
    block = _closing_block(text)
    if block is None:
        return None
    return hashlib.sha256(block.encode("utf-8")).hexdigest()[:16]


def _last_assistant_reply(path: Path) -> Optional[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        role = record.get("role") or record.get("type")
        msg = record.get("message")
        if isinstance(msg, dict):
            role = role or msg.get("role")
            content = msg.get("content")
        else:
            content = record.get("content") or record.get("text")
        if role in ("assistant", "model", "assistant_response"):
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") in ("text", None) and "text" in block
                ]
                if texts:
                    return "\n".join(texts)
    return None


def _check_repeat_closer(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        return None
    t_file = Path(transcript_path)
    if not t_file.is_file():
        return None
    reply = _last_assistant_reply(t_file)
    if not reply:
        return None
    current_hash = _closer_hash(reply)
    if not current_hash:
        return None
    state_dir = _state_directory() / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    hash_file = state_dir / f"{session_id}.closer"
    prev_hash = None
    if hash_file.is_file():
        try:
            prev_hash = hash_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    try:
        hash_file.write_text(current_hash, encoding="utf-8")
    except OSError:
        pass
    if prev_hash and prev_hash == current_hash:
        return "Your last two replies ended on the same list. Do not restate it."
    return None


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--hook"]:
        return run_hook(sys.stdin.read())
    if arguments == ["--chat"]:
        return run_chat_hook(sys.stdin.read())
    if arguments == ["--reminder"]:
        try:
            payload = {}
            import select
            if select.select([sys.stdin], [], [], 0.0)[0]:
                raw = sys.stdin.read()
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        pass
            resolved, _ = effective_preset(Path.cwd())
            inst = resolved.get("instructions") or (resolved.get("preset") or {}).get("instructions") or {}
            reminder_text = inst.get("reminder", "")
            if not reminder_text:
                return 1
            lines = [reminder_text]
            if instructions is not None:
                delta_line = instructions.delta(user_layer(), resolved)
                if delta_line:
                    lines.append(delta_line)
            closer_notice = _check_repeat_closer(payload)
            if closer_notice:
                lines.append(closer_notice)
            notice = _fingerprint_notice(Path(os.environ.get("HOME", str(Path.home()))))
            if notice:
                lines.append(notice)
            print("\n".join(lines))
            return 0
        except Exception:
            return 1
    if arguments == ["--turn"]:
        session_id = None
        try:
            import select
            if select.select([sys.stdin], [], [], 0.0)[0]:
                raw = sys.stdin.read()
                if raw:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        session_id = payload.get("session_id")
        except Exception:
            pass
        record_turn_event(session_id)
        return 0
    print("usage: linter.py --hook | --chat | --turn | --reminder", file=sys.stderr)
    return 64


SUBJECT_MAX = 72
_ANNOUNCING = re.compile(r"^\s*this commit\b", re.IGNORECASE)

# `type(scope): ` in front of the description, per Conventional Commits. The
# mood test reads the description, because the type is never a verb form.
_CONVENTIONAL_PREFIX = re.compile(r"^[a-z]+(?:\([^)]*\))?!?:\s+")
_FIRST_WORD = re.compile(r"[A-Za-z']+")

# The third-person, past and gerund forms of the verbs that open commit
# subjects in practice. A closed list rather than a general mood detector:
# reading mood needs a parser, and a false positive on a gate that refuses
# commits costs more than the extra cases a guess would catch. A subject such
# as "Reset tokens are expired" therefore passes, and the instructions rather
# than the gate are what keep it from being written.
NON_IMPERATIVE_OPENERS = frozenset("""
    added adding adds allowed allowing allows avoided avoiding avoids
    bumped bumping bumps changed changes changing created creates creating
    deleted deletes deleting dropped dropping drops ensured ensures ensuring
    fixed fixes fixing handled handles handling
    implemented implementing implements improved improves improving
    introduced introduces introducing kept keeping keeps letting lets
    made makes making moved moves moving prevented preventing prevents
    refactored refactoring refactors removed removes removing
    renamed renames renaming returned returning returns
    reverted reverting reverts supported supporting supports
    updated updates updating used uses using
""".split())


def _non_imperative_opener(subject: str) -> Optional[str]:
    """The opening word, when it is a form no imperative subject starts with."""
    description = _CONVENTIONAL_PREFIX.sub("", subject.strip(), count=1)
    first = _FIRST_WORD.match(description)
    if first is None:
        return None
    return first.group(0) if first.group(0).lower() in NON_IMPERATIVE_OPENERS else None


def _mask_trailers(text: str) -> str:
    """Space out the message's trailer block so metadata never reads as prose.

    A `Crossrev-pr:` style line is git plumbing, not a sentence: counting it
    toward a word total measures nothing, and rules fire on it as if it were.
    Masking rather than deleting keeps every later line number where it was.
    """
    lines = text.split("\n")
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    start = end
    while start and _TRAILER_LINE.match(lines[start - 1]):
        start -= 1
    # git interpret-trailers reads a trailing paragraph, never the subject
    # and never a Token: value line that still sits inside prose.
    if start == end or start == 0 or lines[start - 1].strip():
        return text
    for index in range(start, end):
        lines[index] = "".join(" " if character != "\n" else "\n" for character in lines[index])
    return "\n".join(lines)


def run_commit_msg(path: str) -> int:
    """Check one commit message. 0 clean, 1 refused, 70 internal error."""
    try:
        if config is not None:
            try:
                user = config.user_config_path()
                proj = config.project_config_path(Path(path))
                local = config.local_config_path(Path(path))
                config.resolve(_rules_dir(), path, user_path=user, project_path=proj, local_path=local, channel="commits")
            except config.ConfigError as err:
                _report_config_error(str(err))
                return 0  # fail open on malformed config

        raw = Path(path).read_text(encoding="utf-8")
        # git appends its template comments, and the whole diff under
        # --verbose. Neither is the author's prose.
        body = raw.split("\n# ------------------------ >8 ---")[0]
        lines = [l for l in body.splitlines() if not l.startswith("#")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return 0  # an empty message is git's business
        subject, message = lines[0], "\n".join(lines)

        findings = []
        if len(subject) > SUBJECT_MAX:
            findings.append(f"1:subject-length:{len(subject)} characters, at most {SUBJECT_MAX}")
        if _ANNOUNCING.match(subject):
            findings.append("1:announcing-opener:" + subject[:60])
        opener = _non_imperative_opener(subject)
        if opener is not None:
            findings.append(
                f'1:imperative-subject:"{opener}" is not imperative. '
                "Write the subject as an instruction."
            )
        # The whole message, subject included. Linting the body alone let a
        # blocking word through in the one line every reader sees, and it
        # reported body findings one line short of where they sit. The subject
        # is still its own unit — it carries no full stop by convention — and
        # the trailer block is masked before any rule reads it.
        findings.extend(
            f.render()
            for f in lint(_mask_trailers(message), path=path, subject_is_own_unit=True)
            if f.severity == "error"
        )

        for finding in findings:
            print(finding, file=sys.stderr)
        return 1 if findings else 0
    except Exception as error:  # noqa: BLE001 - fail open, loudly
        print(f"copydesk: {type(error).__name__}: {error}", file=sys.stderr)
        return 70


# --- The staged-prose gate -------------------------------------------------
#
# A pre-commit hook judges what this commit adds, not the file. The decision
# is the write gate's, moved to a third pipeline position: findings are
# attributed to the changed text, document-scoped rules block only when they
# newly fire against the HEAD version, and a severity of warn never blocks.


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git_output(args: list[str], cwd: Path) -> Optional[str]:
    """One answer from git, or None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _staged_markdown(work: Path) -> Optional[list[tuple[str, str]]]:
    """Staged Markdown as (base_path_in_HEAD_or_empty, index_path) pairs.

    Renames keep their source path so a moved file is judged against what it
    moved from; without that, a pure rename of an imperfect file would read
    as brand-new prose. Deletions never appear here.
    """
    out = _git_output(
        [
            "diff", "--cached", "--find-renames", "--name-status", "-z",
            "--diff-filter=ACMR", "--", "*.md",
        ],
        work,
    )
    if out is None:
        return None
    entries = out.split("\0")
    staged: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        token = entries[index]
        if not token:
            break
        status = token[0]
        index += 1
        if status in ("R", "C") and index + 1 < len(entries):
            old_path = entries[index]
            new_path = entries[index + 1]
            index += 2
            staged.append((old_path, new_path))
            continue
        if index < len(entries):
            staged.append(("", entries[index]))
            index += 1
    return staged


def _added_char_ranges(diff_text: str, masked_proposed: str) -> list[tuple[int, int]]:
    """Char spans of git's added lines inside the masked staged document.

    ``git diff --cached --unified=0`` is the source when the head/index pair
    is past the character matcher's cap: hunks are authoritative whatever the
    document size. A hunk with nothing on the ``+`` side is a deletion; it
    records a zero-width point at the join so a sentence the deletion merged
    can still be charged, matching the write-time treatment.
    """
    lines = masked_proposed.split("\n")
    offsets = []
    total = 0
    for line in lines:
        offsets.append(total)
        total += len(line) + 1
    ranges: list[tuple[int, int]] = []
    for header in diff_text.splitlines():
        match = _HUNK_HEADER.match(header)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count == 0:
            point = offsets[start] if start < len(offsets) else total
            ranges.append((point, point))
            continue
        lo = offsets[start - 1]
        hi = offsets[start + count - 2] + len(lines[start + count - 2])
        ranges.append((lo, hi))
    return ranges


def _git_operation_in_progress(work: Path) -> Optional[str]:
    """The operation replaying someone else's prose, or None.

    Merge, cherry-pick and revert each set a ref while they are stopped. A
    rebase sets none of them and leaves a `rebase-merge` or `rebase-apply`
    directory; `git am` marks that directory with an `applying` file;
    `git merge --squash` leaves `SQUASH_MSG`; and the `--no-commit` forms of
    merge, cherry-pick and revert set no ref at all and leave `MERGE_MSG`.
    The manual commit that finishes any of them does run the hook.

    Those are every state `git status` itself reports, bisect excepted: a
    commit made during a bisect carries the committer's own prose, so it is
    judged like any other.

    Every marker here is a safe signal because git clears it when the commit
    lands. `REBASE_HEAD` is not, and is deliberately absent: it still
    resolves after the rebase finishes, so keying on it would silence the
    gate for every commit that followed.

    The ref checks run first, so a conflicted merge is still named a merge
    rather than falling through to the `MERGE_MSG` it also leaves.
    """
    for name, ref in (
        ("merge", "MERGE_HEAD"),
        ("cherry-pick", "CHERRY_PICK_HEAD"),
        ("revert", "REVERT_HEAD"),
    ):
        if _git_output(["rev-parse", "--verify", "-q", ref], work) is not None:
            return name
    for name, marker in (
        ("rebase", "rebase-merge"),
        # `git am` shares rebase-apply and marks itself with `applying`, so
        # the more specific probe runs first and the message names the
        # command the user ran.
        ("patch application", "rebase-apply/applying"),
        ("rebase", "rebase-apply"),
        ("squash merge", "SQUASH_MSG"),
        ("a --no-commit merge, cherry-pick or revert", "MERGE_MSG"),
    ):
        located = _git_output(["rev-parse", "--git-path", marker], work)
        if located is None:
            continue
        candidate = Path(located.strip())
        if not candidate.is_absolute():
            candidate = work / candidate
        if candidate.exists():
            return name
    return None


def _staged_pathspec(index_path: str, base_path: str) -> list[str]:
    """Both sides of a rename, because git needs both to detect one.

    Rename detection pairs a deletion with an addition, and a pathspec naming
    only the new path filters the deletion out before the pairing runs. Git
    then reports the file as new and emits one hunk covering all of it, so
    every line of a renamed document reads as added.
    """
    if base_path and base_path != index_path:
        return [index_path, base_path]
    return [index_path]


def _staged_changed_ranges(
    base_text: str, staged_text: str, work: Path, index_path: str, base_path: str = ""
) -> list[tuple[int, int]]:
    """Where the staged version differs from its HEAD version.

    The character matcher first: it narrows a replaced block to the characters
    that actually moved, so an untouched sentence sharing a line with an edit
    is not charged. Past the size cap the diff hunks stand in, the same role
    the edit's own bounds play for the write-time gate.
    """
    masked_base = exclude_markdown(base_text)
    masked_staged = exclude_markdown(staged_text)
    try:
        return _changed_char_ranges(masked_base, masked_staged)
    except _TooLargeToCompare:
        diff_text = _git_output(
            ["diff", "--cached", "--find-renames", "--unified=0", "--",
             *_staged_pathspec(index_path, base_path)],
            work,
        )
        if diff_text is None:
            # Unattributable and unbounded: charge nothing rather than block
            # on metadata CopyDesk could not read.
            print(
                f"warning: {index_path}: too large to compare and no diff available; "
                "its added lines are not attributed",
                file=sys.stderr,
            )
            return []
        return _added_char_ranges(diff_text, masked_staged)


def _staged_adds_lines(work: Path, index_path: str, base_path: str = "") -> bool:
    """Whether the staged diff adds lines, failing safe toward checking.

    Both paths and rename detection, for the same reason the hunk fallback
    needs them: without the old path a rename reads as a deletion line and an
    addition line, and the first of those says nothing was added.
    """
    numstat = _git_output(
        ["diff", "--cached", "--find-renames", "--numstat", "--",
         *_staged_pathspec(index_path, base_path)],
        work,
    )
    if numstat is None:
        return True
    total, seen = 0, False
    for line in numstat.splitlines():
        if not line.strip():
            continue
        seen = True
        additions = line.split("\t", 1)[0]
        if additions == "-":
            return True
        try:
            total += int(additions)
        except ValueError:
            return True
    return True if not seen else total > 0


def run_staged(cwd: Union[str, Path, None] = None) -> int:
    """Check every staged Markdown file against the text it adds.

    The index is judged, not the working tree: ``git show :path`` reads what
    will be committed and nothing else. Git resolves a bare pathspec against
    the current directory but reports names relative to the repository root,
    so the root is resolved once and every call - the pathspec, the blob
    reads, the display path - runs from there, whatever directory started
    the command. A finding blocks only when the text that carries it is new
    relative to HEAD, or when a document-scoped rule fires now and did not
    fire in HEAD. Warn-severity findings report and never block, whatever
    the channel decides. 0 clean, 1 refused.
    """
    try:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
        start = Path(cwd) if cwd is not None else Path.cwd()
        root = _git_output(["rev-parse", "--show-toplevel"], start)
        if root is None:
            print("error: copydesk check --staged needs a git repository", file=sys.stderr)
            return 64
        work = Path(root.strip())
        operation = _git_operation_in_progress(work)
        if operation is not None:
            print(f"copydesk: {operation} in progress; staged prose was not judged")
            return 0
        staged_files = _staged_markdown(work)
        if staged_files is None:
            print("error: copydesk check --staged needs a git repository", file=sys.stderr)
            return 64
        has_head = _git_output(["rev-parse", "--verify", "HEAD^{commit}"], work) is not None

        refused = False
        for base_path, path in staged_files:
            staged = _git_output(["show", f":{path}"], work)
            if staged is None:
                print(f"warning: {path}: staged content unreadable; skipped", file=sys.stderr)
                continue
            display = str((work / path).resolve())
            resolved, _ = effective_preset(display)
            decision = channels.decide(display, resolved)
            if decision.action == "ignore":
                print(f"{path}: not checked (paths.ignore)")
                continue

            t_start = time.time()
            findings = lint(staged, path=display)
            duration_ms = round((time.time() - t_start) * 1000, 1)

            base = ""
            if has_head:
                base = _git_output(["show", f"HEAD:{base_path or path}"], work) or ""
            origined = _attribute_origins(
                findings, exclude_markdown(staged),
                _staged_changed_ranges(base, staged, work, path, base_path)
            )

            scoped = [
                f for f in origined
                if f.check in DOCUMENT_SCOPED_BLOCKING_RULES and f.severity == "error"
            ]
            adds_lines = _staged_adds_lines(work, path, base_path)
            # lint(HEAD) is the expensive half of this loop, so read it only
            # where a decision below turns on it.
            wants_previous = bool(base) and (
                not adds_lines or (bool(scoped) and decision.action != "warn")
            )
            previous_errors: set[str] = set()
            if wants_previous:
                previous_errors = {
                    f.check for f in lint(base, path=display) if f.severity == "error"
                }

            if not adds_lines and previous_errors:
                # A commit that adds no lines wrote no prose, so it answers
                # only for a rule HEAD did not already break. A deletion that
                # joins two sentences owns the sentence it made; a paragraph
                # already over length before the deletion stays pre-existing.
                # Attribution cannot tell those apart on its own, because a
                # deletion is a zero-width point and every span containing it
                # reads as touched. Relabelling rather than filtering keeps
                # the printed findings and the pre-existing count agreeing
                # with what blocked.
                origined = [
                    _replace_field(f, origin="existing")
                    if f.origin == "new" and f.check in previous_errors else f
                    for f in origined
                ]

            blocking = blocking_findings_for_retry(origined)
            if scoped and decision.action != "warn" and adds_lines:
                # Document-scoped rules have no hunk to belong to. They block
                # when added lines make them newly fire: absent from lint(HEAD)
                # and present in the staged text.
                blocking = blocking + [f for f in scoped if f.check not in previous_errors]
            seen: set[Finding] = set()
            blocking = [f for f in blocking if not (f in seen or seen.add(f))]  # type: ignore[arg-type]

            blocked_here = bool(blocking) and decision.action != "warn"
            refused = refused or blocked_here

            shown = [f for f in origined if f.origin == "new"] if not blocked_here else list(blocking)
            if shown:
                print(f"{path}:")
                for finding in shown:
                    print(finding.render())
            preexisting = sum(
                1 for f in origined if f.severity == "error" and f.origin != "new" and f not in blocking
            )
            if blocked_here:
                print(
                    f"{path}: refused. Commit only the lines you wrote; "
                    "git commit --no-verify skips this check."
                )
            if preexisting:
                print(f"{path}: {preexisting} pre-existing finding(s) left alone.")

            doc_bytes = len(staged.encode("utf-8"))
            body_sentences = len(_sentence_records(exclude_markdown(staged)))
            rollups = _finding_rollups(origined)
            _record_event({
                "ts": round(time.time(), 1),
                "event": "lint",
                "surface": "pre-commit",
                "tool": None,
                "path": display,
                "decision": "block" if blocked_here else ("warn" if decision.action == "warn" else "pass"),
                "streak": 0,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_bytes": doc_bytes,
                "payload_words": len(staged.split()),
                "sentences": body_sentences,
                "findings_total": len(origined),
                "origin_totals": rollups["origin_totals"],
                "rule_totals": rollups["rule_totals"],
                "blocking_origin_totals": rollups["blocking_origin_totals"],
                "blocking_rule_totals": rollups["blocking_rule_totals"],
                "findings": _serialize_findings(origined),
            })
        return 1 if refused else 0
    except Exception as error:  # noqa: BLE001 - fail open, loudly
        print(f"copydesk: {type(error).__name__}: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
