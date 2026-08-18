#!/usr/bin/env python3
"""Plain English Markdown linter.

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

from dataclasses import dataclass
import datetime
import fcntl
import hashlib
import json
from math import sqrt
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Iterable, Optional, Union


FILE_STATISTICS_MIN_SENTENCES = 15
LONG_SENTENCE_WARNING_WORDS = 25
LONG_SENTENCE_ERROR_WORDS = 40
LONG_SENTENCE_RATE = 0.10
AVG_SENTENCE_MIN_WORDS = 12
AVG_SENTENCE_MAX_WORDS = 20
MIN_SENTENCE_VARIATION = 4.0
RETRY_LIMIT = 3
STATE_TTL_SECONDS = 24 * 60 * 60
ROTATION_SIZE_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_STORED_FINDINGS = 20


@dataclass(frozen=True)
class Finding:
    """One line-oriented check result for the command or hook."""

    line: int
    check: str
    excerpt: str
    severity: str
    origin: str = "new"

    def render(self) -> str:
        return f"{self.line}:{self.check}:{self.excerpt}"


@dataclass(frozen=True)
class Sentence:
    """A sentence produced by the upstream splitter, with its source line."""

    text: str
    line: int

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


# This inventory is verified by tests/test_rules_sync.py. Its text mirrors the
# mechanically enforceable quoted phrases in the Decision 2 rules block.
RULE_PATTERNS: tuple[RulePattern, ...] = (
    RulePattern("as noted above", _compiled(r"\bas noted above\b"), "orphan-pointer", "error"),
    RulePattern("as mentioned earlier", _compiled(r"\bas mentioned earlier\b"), "orphan-pointer", "error"),
    RulePattern("the former", _compiled(r"\bthe former\b"), "orphan-pointer", "error"),
    RulePattern("the latter", _compiled(r"\bthe latter\b"), "orphan-pointer", "error"),
    RulePattern("per point N", _compiled(r"\bper point \d+\b"), "orphan-pointer", "error"),
    RulePattern("see above", _compiled(r"\bsee above\b"), "orphan-pointer", "error"),
    RulePattern("seam", _compiled(r"\bseams?\b"), "banned-word", "error"),
    RulePattern("load-bearing", _compiled(r"\bload-bearing\b"), "banned-word", "error"),
    RulePattern("blast radius", _compiled(r"\bblast radius\b"), "banned-word", "error"),
    RulePattern("affordance", _compiled(r"\baffordances?\b"), "banned-word", "error"),
    RulePattern("first-class", _compiled(r"\bfirst-class\b"), "banned-word", "error"),
    RulePattern("escape hatch", _compiled(r"\bescape hatch\b"), "banned-word", "error"),
    RulePattern("actually", _compiled(r"\bactually\b"), "banned-word", "error"),
    RulePattern("genuinely", _compiled(r"\bgenuinely\b"), "banned-word", "error"),
    RulePattern("simply", _compiled(r"\bsimply\b"), "banned-word", "error"),
    RulePattern("basically", _compiled(r"\bbasically\b"), "banned-word", "error"),
    RulePattern("really", _compiled(r"\breally\b"), "banned-word", "error"),
    RulePattern("effectively", _compiled(r"\beffectively\b"), "banned-word", "error"),
    RulePattern("essentially", _compiled(r"\bessentially\b"), "banned-word", "error"),
    RulePattern("fundamentally", _compiled(r"\bfundamentally\b"), "banned-word", "error"),
    RulePattern("materially", _compiled(r"\bmaterially\b"), "banned-word", "error"),
    RulePattern("arguably", _compiled(r"\barguably\b"), "banned-word", "error"),
    RulePattern("meaningfully", _compiled(r"\bmeaningfully\b"), "banned-word", "error"),
    RulePattern("honestly", _compiled(r"\bhonestly\b"), "banned-word", "error"),
    RulePattern("delve", _compiled(r"\bdelv\w*\b"), "banned-word", "error"),
    RulePattern("utilize", _compiled(r"\butiliz\w*\b"), "banned-word", "error"),
    RulePattern("it's worth noting", _compiled(r"\bit(?:'|’)s worth noting\b"), "banned-word", "error"),
    RulePattern("a testament to", _compiled(r"\ba testament to\b"), "banned-word", "error"),
    RulePattern("crucial", _compiled(r"\bcrucial\b"), "banned-word", "error"),
    RulePattern("pivotal", _compiled(r"\bpivotal\b"), "banned-word", "error"),
    RulePattern("showcase", _compiled(r"\bshowcas\w*\b"), "banned-word", "error"),
    RulePattern("intricate", _compiled(r"\bintricat\w*\b"), "banned-word", "error"),
    RulePattern("robust", _compiled(r"\brobust\b"), "banned-word", "error"),
    RulePattern("comprehensive", _compiled(r"\bcomprehensive\b"), "banned-word", "error"),
    RulePattern("surface", _compiled(r"\bsurfac(?:e|ed|es|ing)\b"), "verb-jargon", "warning"),
    RulePattern("land", _compiled(r"\bland(?:s|ed|ing)?\b"), "verb-jargon", "warning"),
    RulePattern("leverage", _compiled(r"\bleverag\w*\b"), "verb-jargon", "warning"),
    RulePattern("underscore", _compiled(r"\bunderscor\w*\b"), "verb-jargon", "warning"),
    RulePattern("landscape", _compiled(r"\blandscape\b"), "verb-jargon", "warning"),
    RulePattern(
        "It's not just X — it's Y",
        _compiled(r"\b(?:it\s+(?:is|was)|it(?:'|’)s)?\s*not just\b.{0,160}?(?:\bbut\b|[—–-]\s*(?:it\s+(?:is|was)|it(?:'|’)s)\b)"),
        "contrast-construction",
        "error",
    ),
    RulePattern("say the word", _compiled(r"\bsay the word\b"), "soft-offer", "error"),
    RulePattern("just let me know", _compiled(r"\bjust let me know\b"), "soft-offer", "error"),
    RulePattern("happy to", _compiled(r"\bhappy to\b"), "soft-offer", "error"),
    RulePattern("feel free to", _compiled(r"\bfeel free to\b"), "soft-offer", "error"),
    RulePattern("if you'd like", _compiled(r"\bif you(?:'|’)d like\b"), "soft-offer", "error"),
    RulePattern("would you like", _compiled(r"\bwould you like\b"), "soft-offer", "error"),
    RulePattern("want me to", _compiled(r"\bwant me to\b"), "soft-offer", "error"),
    RulePattern("should I continue", _compiled(r"\bshould i continue\b"), "soft-offer", "error"),
    RulePattern("I hope this helps", _compiled(r"\bi hope this helps\b"), "soft-offer", "error"),
    RulePattern("Great question", _compiled(r"(?m)^[ \t]*great question\b"), "announcing-opener", "error"),
    RulePattern("Let me…", _compiled(r"(?m)^[ \t]*let me\b"), "announcing-opener", "error"),
    RulePattern("I'll…", _compiled(r"(?m)^[ \t]*i(?:'|’)ll\b"), "announcing-opener", "error"),
    RulePattern("Sure!", _compiled(r"(?m)^[ \t]*sure(?:!|\b)"), "announcing-opener", "error"),
    RulePattern("Looking at your…", _compiled(r"(?m)^[ \t]*looking at your\b"), "announcing-opener", "error"),
    RulePattern("To answer your question…", _compiled(r"(?m)^[ \t]*to answer your question\b"), "announcing-opener", "error"),
    RulePattern("Moreover", _compiled(r"(?m)^[ \t]*moreover\b"), "announcing-opener", "error"),
    RulePattern("Furthermore", _compiled(r"(?m)^[ \t]*furthermore\b"), "announcing-opener", "error"),
    RulePattern("Additionally", _compiled(r"(?m)^[ \t]*additionally\b"), "announcing-opener", "error"),
    RulePattern("In conclusion", _compiled(r"(?m)^[ \t]*in conclusion\b"), "announcing-opener", "error"),
    RulePattern("circle back", _compiled(r"\bcircle back\b"), "idiom", "error"),
    RulePattern("get the ball rolling", _compiled(r"\bget the ball rolling\b"), "idiom", "error"),
    RulePattern("on the same page", _compiled(r"\bon the same page\b"), "idiom", "error"),
    RulePattern("moving forward", _compiled(r"\bmoving forward\b"), "idiom", "error"),
    RulePattern("Uh oh", _compiled(r"(?m)^[ \t]*uh oh\b"), "announcing-opener", "error"),
    RulePattern("Oh no", _compiled(r"(?m)^[ \t]*oh no\b"), "announcing-opener", "error"),
    RulePattern("There seems to be a problem", _compiled(r"(?m)^[ \t]*there seems to be a problem\b"), "announcing-opener", "error"),
    RulePattern("sentence-initial This/That", _compiled(r"(?m)^[ \t]*(?:this|that)\b"), "orphan-pointer", "error"),
)

# The rules block also quotes advisory instructions and worked examples that a
# regex must not enforce. Keep their exact text in the same inventory, so a
# rules-block edit cannot silently escape the sync test. RULE_PATTERNS remains
# the executable subset; test_checks.py proves its behavior separately.
CANONICAL_REFERENCE_PHRASES = (
    "About 15 minutes if tests cover this, an afternoon if not",
    "I've now updated the schema, added the index and adjusted the migration, which means…",
    "Login works with magic links. Try `npm run dev`, open `/login`.",
    "Step 3 of 5 done, next is the backfill.",
    "What are my options",
    "and",
    "anything else?",
    "by the way",
    "clean",
    "do now",
    "elaborate",
    "found",
    "landed",
    "lands",
    "later",
    "raised",
    "shipped",
    "showed",
    "some work",
    "still broken",
    "that",
    "this",
)

PATTERN_TEXTS = tuple(pattern.phrase for pattern in RULE_PATTERNS) + CANONICAL_REFERENCE_PHRASES
AI_TELL_PHRASES = frozenset(
    {
        "delve",
        "utilize",
        "it's worth noting",
        "a testament to",
        "crucial",
        "pivotal",
        "showcase",
        "intricate",
    }
)


_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?^(?:---|\.\.\.)[ \t]*(?:\n|$)", re.MULTILINE | re.DOTALL)
_FENCED_CODE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*(?:\n|$)")
_BLOCKQUOTE = re.compile(r"(?m)^[ \t]*>.*(?:\n|$)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://[^\s)\]>]+")
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+.*(?:\n|$)")
_LIST_MARKER = re.compile(r"^\s*(?:[-*]|\d+\.)\s+", re.MULTILINE)
_LIST_LINE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:])\s+")


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
    """Use SimpleEnglish's list handling and sentence splitter unchanged."""
    text = _LIST_MARKER.sub("", text)
    parts = _SENTENCE_SPLIT.split(text)
    return [part.strip() for part in parts if len(part.strip().split()) >= 2]


def _sentence_records(text: str) -> list[Sentence]:
    """Apply the same splitter while retaining a source line for each sentence."""
    normalized = _LIST_MARKER.sub(lambda match: " " * len(match.group(0)), text)
    parts = _SENTENCE_SPLIT.split(normalized)
    records: list[Sentence] = []
    cursor = 0
    for part in parts:
        position = normalized.find(part, cursor)
        if position < 0:
            continue
        cursor = position + len(part)
        stripped = part.strip()
        if len(stripped.split()) < 2:
            continue
        leading = len(part) - len(part.lstrip())
        line = normalized.count("\n", 0, position + leading) + 1
        records.append(Sentence(stripped, line))
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


def _document_is_exempt(path: Optional[Union[str, Path]], text: str) -> bool:
    name = Path(path).name.lower() if path else ""
    if any(token in name for token in ("checklist", "changelog", "roadmap", "status", "toc", "table-of-contents")):
        return True

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    list_lines = sum(1 for line in lines if _LIST_LINE.match(line))
    return list_lines * 2 > len(lines)


def _paragraph_findings(text: str, *, exempt: bool) -> Iterable[Finding]:
    if exempt:
        return ()
    findings: list[Finding] = []
    cursor = 0
    for paragraph in re.split(r"\n[ \t]*\n", text):
        position = text.find(paragraph, cursor)
        cursor = max(cursor, position + len(paragraph))
        if not paragraph.strip() or all(_LIST_LINE.match(line) for line in paragraph.splitlines() if line.strip()):
            continue
        paragraph_sentences = _sentence_records(paragraph)
        if len(paragraph_sentences) > 4:
            line = _line_number(text, max(0, position))
            findings.append(Finding(line, "paragraph-length", _excerpt(paragraph), "error"))
    return findings


def _pattern_findings(text: str) -> Iterable[Finding]:
    findings: list[Finding] = []
    for pattern in RULE_PATTERNS:
        for match in pattern.regex.finditer(text):
            findings.append(
                Finding(
                    _line_number(text, match.start()),
                    pattern.check,
                    _line_excerpt(text, match.start()),
                    pattern.severity,
                )
            )
    return findings


def _nested_table_findings(text: str) -> Iterable[Finding]:
    # Tables are excluded from prose checks, but this check needs to see their
    # indentation. All other exclusions stay active.
    visible = exclude_markdown(text, exclude_tables=False)
    lines = visible.splitlines()
    findings: list[Finding] = []
    for index, line in enumerate(lines):
        if not re.match(r"^[ \t]+\|", line):
            continue
        for preceding in reversed(lines[:index]):
            if not preceding.strip():
                break
            if _LIST_LINE.match(preceding):
                findings.append(Finding(index + 1, "nested-table", _excerpt(line), "error"))
                break
    return findings


def lint(text: str, path: Optional[Union[str, Path]] = None) -> list[Finding]:
    """Return deterministic checks for one Markdown document.

    The function is deliberately dependency-free so the CLI, hook, and future
    measurement scripts can import exactly the same exclusions and rules.
    """
    body = exclude_markdown(text)
    records = _sentence_records(body)
    exempt = _document_is_exempt(path, body)
    findings: list[Finding] = []

    for sentence in records:
        if sentence.words > LONG_SENTENCE_ERROR_WORDS:
            findings.append(Finding(sentence.line, "sentence-length", _excerpt(sentence.text), "error"))
        elif sentence.words > LONG_SENTENCE_WARNING_WORDS:
            findings.append(Finding(sentence.line, "sentence-length", _excerpt(sentence.text), "warning"))

    findings.extend(_paragraph_findings(body, exempt=exempt))

    if not exempt and len(records) >= FILE_STATISTICS_MIN_SENTENCES:
        long_sentences = [sentence for sentence in records if sentence.words > LONG_SENTENCE_WARNING_WORDS]
        if len(long_sentences) * 10 > len(records):
            findings.append(
                Finding(
                    1,
                    "long-sentence-rate",
                    f"{len(long_sentences)}/{len(records)} qualifying sentences exceed {LONG_SENTENCE_WARNING_WORDS} words",
                    "error",
                )
            )

        average = sum(sentence.words for sentence in records) / len(records)
        if average < AVG_SENTENCE_MIN_WORDS or average > AVG_SENTENCE_MAX_WORDS:
            findings.append(
                Finding(1, "avg-sentence-length", f"average sentence length is {average:.1f} words", "warning")
            )

        variation = sqrt(sum((sentence.words - average) ** 2 for sentence in records) / len(records))
        if variation < MIN_SENTENCE_VARIATION:
            findings.append(
                Finding(1, "sentence-variation", f"sentence length variation is {variation:.1f} words", "warning")
            )

    findings.extend(_pattern_findings(body))
    findings.extend(_nested_table_findings(text))
    return sorted(findings, key=lambda finding: (finding.line, finding.severity != "error", finding.check, finding.excerpt))


def has_blocking_findings(findings: Iterable[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


def has_ai_tell_finding(text: str, findings: Iterable[Finding]) -> bool:
    if not any(finding.check == "banned-word" and finding.severity == "error" for finding in findings):
        return False
    return any(pattern.phrase in AI_TELL_PHRASES and pattern.regex.search(text) for pattern in RULE_PATTERNS)


def _compute_edit_origins(
    findings: list[Finding],
    existing: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    reconstructed: str,
) -> list[Finding]:
    if not new_string:
        # Pure deletion: no lines inserted, all remaining findings are existing
        return [Finding(f.line, f.check, f.excerpt, f.severity, origin="existing") for f in findings]

    occurrences: list[int] = []
    search_pos = 0
    while True:
        idx = existing.find(old_string, search_pos)
        if idx < 0:
            break
        occurrences.append(idx)
        if not replace_all:
            break
        search_pos = idx + len(old_string)

    if not occurrences:
        return [Finding(f.line, f.check, f.excerpt, f.severity, origin="existing") for f in findings]

    delta = len(new_string) - len(old_string)
    inserted_line_ranges: list[tuple[int, int]] = []
    for i, orig_pos in enumerate(occurrences):
        prop_start = orig_pos + i * delta
        prop_end = prop_start + len(new_string)
        start_line = reconstructed.count("\n", 0, prop_start) + 1
        end_line = reconstructed.count("\n", 0, max(prop_start, prop_end - 1)) + 1
        inserted_line_ranges.append((start_line, end_line))

    result: list[Finding] = []
    for f in findings:
        is_new = any(start <= f.line <= end for start, end in inserted_line_ranges)
        result.append(Finding(f.line, f.check, f.excerpt, f.severity, origin="new" if is_new else "existing"))
    return result


def _serialize_findings(findings: list[Finding]) -> list[dict[str, object]]:
    log_text = os.environ.get("PLAIN_ENGLISH_LOG_FLAGGED_TEXT") != "0"
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
    configured = os.environ.get("PLAIN_ENGLISH_STATE_DIR")
    return Path(configured) if configured else Path.home() / ".claude" / "plain-english"


def _state_path(state_dir: Path, session_id: str) -> Path:
    safe_session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    return state_dir / f"{safe_session_id}.json"


def _sweep_state(state_dir: Path, now: float) -> None:
    for candidate in state_dir.glob("*.json"):
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


def _record_event(event: dict[str, object]) -> None:
    if os.environ.get("PLAIN_ENGLISH_LOG") == "0":
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


def _proposed_document(payload: object) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return the path, proposed Markdown, and session id or fail open."""
    if not isinstance(payload, dict):
        return None, None, None

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id")
    if tool_name not in {"Write", "Edit"} or not isinstance(tool_input, dict):
        return None, None, None
    if not isinstance(session_id, str) or not session_id:
        return None, None, None

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path.lower().endswith(".md"):
        return None, None, None

    if tool_name == "Write":
        content = tool_input.get("content")
        return (file_path, content, session_id) if isinstance(content, str) else (None, None, None)

    old_string = tool_input.get("old_string")
    new_string = tool_input.get("new_string")
    replace_all = tool_input.get("replace_all")
    if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str) or not isinstance(replace_all, bool):
        return None, None, None

    try:
        existing = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return None, None, None
    if old_string not in existing:
        return None, None, None

    occurrences = -1 if replace_all else 1
    return file_path, existing.replace(old_string, new_string, occurrences), session_id


def _warning_for_retry(hashes: list[str]) -> str:
    if len(set(hashes)) == 1:
        detail = f"same content submitted 3 times (sha256={hashes[-1]})"
    elif len(set(hashes)) == RETRY_LIMIT:
        detail = f"3 different attempts still failing (sha256={', '.join(hashes)})"
    else:
        detail = f"3 attempts still failing (sha256={', '.join(hashes)})"
    return f"Plain English gate passed after 3 failed attempts: {detail}. Run /humanizer before the next edit."


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

        file_path, proposed, session_id = _proposed_document(payload)
        if file_path is None or proposed is None or session_id is None:
            return 0

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
            replace_all = bool(tool_input.get("replace_all", False)) if isinstance(tool_input, dict) else False
            try:
                existing = Path(file_path).read_text(encoding="utf-8")
            except OSError:
                existing = ""
            findings_with_origin = _compute_edit_origins(findings, existing, old_string, new_string, replace_all, proposed)
            payload_bytes = len(old_string.encode("utf-8")) + len(new_string.encode("utf-8"))
            payload_words = len(old_string.split()) + len(new_string.split())

        doc_bytes = len(proposed.encode("utf-8"))
        body_sentences = len(_sentence_records(exclude_markdown(proposed)))
        findings_total = len(findings)

        state_dir = _state_directory()
        now = time.time()
        state_dir.mkdir(parents=True, exist_ok=True)
        _sweep_state(state_dir, now)
        state_path = _state_path(state_dir, session_id)
        state = _read_state(state_path, now)
        files = state["files"]
        if not isinstance(files, dict):
            return 0

        if not has_blocking_findings(findings):
            files.pop(file_path, None)
            _write_state(state_path, state)
            _record_event({
                "ts": round(now, 1),
                "event": "lint",
                "surface": "gate",
                "tool": tool_name,
                "path": str(file_path),
                "decision": "pass",
                "streak": 0,
                "duration_ms": duration_ms,
                "bytes": doc_bytes,
                "payload_bytes": payload_bytes,
                "payload_words": payload_words,
                "sentences": body_sentences,
                "findings_total": findings_total,
                "findings": _serialize_findings(findings_with_origin),
                "session_id": session_id,
            })
            return 0

        content_hash = hashlib.sha256(proposed.encode("utf-8")).hexdigest()
        previous = files.get(file_path)
        previous_hashes = previous.get("hashes", []) if isinstance(previous, dict) else []
        hashes = [value for value in previous_hashes if isinstance(value, str)][-2:] + [content_hash]
        streak = (previous.get("streak", 0) if isinstance(previous, dict) else 0) + 1

        if streak >= RETRY_LIMIT:
            files.pop(file_path, None)
            _write_state(state_path, state)
            _write_retry_warning(_warning_for_retry(hashes))
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
            "findings": _serialize_findings(findings_with_origin),
            "session_id": session_id,
        })
        for finding in findings:
            if finding.severity == "error":
                print(finding.render(), file=sys.stderr)
        if has_ai_tell_finding(proposed, findings):
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

    events.sort(key=lambda e: float(e.get("ts", 0)))

    if since:
        current_time = now if now is not None else time.time()
        cutoff = _parse_since(since, current_time)
        if cutoff is not None:
            events = [e for e in events if float(e.get("ts", 0)) >= cutoff]

    return events


def get_prevention_summary(
    results_dir: Optional[Path] = None,
    now: Optional[float] = None,
) -> Optional[dict[str, object]]:
    if results_dir is None:
        bundle_root = Path(__file__).resolve().parents[1]
        results_dir = bundle_root / "eval" / "results"

    if not results_dir.is_dir():
        return None

    summary_files = sorted(results_dir.glob("*-summary.json"))
    if not summary_files:
        return None

    latest_file = summary_files[-1]
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


def summarize_events(
    events: list[dict[str, object]],
    now: Optional[float] = None,
    prevention_dir: Optional[Path] = None,
) -> dict[str, object]:
    current_time = now if now is not None else time.time()
    lint_events = [e for e in events if e.get("event") == "lint"]
    turn_events = [e for e in events if e.get("event") == "turn"]

    if events:
        min_ts = min(float(e.get("ts", current_time)) for e in events)
        max_ts = max(float(e.get("ts", current_time)) for e in events)
    else:
        min_ts = current_time
        max_ts = current_time

    start_date = datetime.datetime.fromtimestamp(min_ts).strftime("%Y-%m-%d")
    end_date = datetime.datetime.fromtimestamp(max_ts).strftime("%Y-%m-%d")
    window_days = max(1, round((max_ts - min_ts) / 86400) + 1) if events else 0

    total_writes = len(lint_events)
    passed_first = sum(1 for e in lint_events if e.get("decision") == "pass" and int(e.get("streak", 0)) == 0)
    blocked = sum(1 for e in lint_events if e.get("decision") == "block")
    escaped = sum(1 for e in lint_events if e.get("decision") == "escape")

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

    for e in lint_events:
        decision = e.get("decision")
        streak = int(e.get("streak", 0))
        p_words = int(e.get("payload_words", 0))
        file_path = str(e.get("path", ""))
        session_id = str(e.get("session_id", ""))
        findings = e.get("findings", [])
        if isinstance(findings, list):
            for f in findings:
                if isinstance(f, dict):
                    rule = str(f.get("rule", ""))
                    if rule:
                        top_rules_counts[rule] = top_rules_counts.get(rule, 0) + 1

        if streak > 1:
            rework_rewrites += 1
            rework_words += p_words
            key = (session_id, file_path)
            if key not in session_file_rework:
                session_file_rework[key] = []
            session_file_rework[key].append(p_words)

        if decision == "block":
            file_block_counts[file_path] = file_block_counts.get(file_path, 0) + 1
            origins = []
            block_rules = set()
            if isinstance(findings, list):
                for f in findings:
                    if isinstance(f, dict):
                        orig = str(f.get("origin", "new"))
                        origins.append(orig)
                        rule = str(f.get("rule", ""))
                        if rule:
                            block_rules.add(rule)

            is_false = False
            if origins:
                if all(o == "new" for o in origins):
                    new_only_blocks += 1
                elif all(o == "existing" for o in origins):
                    existing_only_blocks += 1
                    is_false = True
                    file_existing_counts[file_path] = file_existing_counts.get(file_path, 0) + 1
                else:
                    mixed_blocks += 1
                    is_false = True
            else:
                new_only_blocks += 1

            for r in block_rules:
                rework_by_rule_counts[r] = rework_by_rule_counts.get(r, 0) + 1
                rework_by_rule_words[r] = rework_by_rule_words.get(r, 0) + p_words
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

    weekly_events: dict[str, list[dict[str, object]]] = {}
    for e in lint_events:
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

    prevention = get_prevention_summary(prevention_dir, now=current_time)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": window_days,
        "total_events": len(events),
        "lint_events_count": total_writes,
        "turn_events_count": len(turn_events),
        "work": {
            "total_writes": total_writes,
            "passed_first": passed_first,
            "passed_first_rate": passed_first_rate,
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
            "runs": total_writes,
        },
        "cost": {
            "reminder_turns": len(turn_events),
            "reminder_words": len(turn_events) * 76,
            "reminder_tokens_est": round(len(turn_events) * 76 * 4 / 3),
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
            "total_events": total_writes,
        },
    }


def _format_token_k(tokens: int) -> str:
    if tokens >= 1000:
        return f"~{tokens // 1000}k"
    return f"~{tokens}"


def _format_bar(rate: float) -> str:
    count = int(round(rate * 0.85))
    return "#" * count


def format_stats_terminal(summary: dict[str, object]) -> str:
    lines: list[str] = []
    start_date = summary["start_date"]
    end_date = summary["end_date"]
    days = summary["days"]

    lines.append(f"Plain English — {start_date} to {end_date} ({days} days)")
    lines.append("")

    work = summary["work"]
    assert isinstance(work, dict)
    lines.append("Work")
    lines.append(f"  Markdown writes seen          {work['total_writes']:>3}")
    lines.append(f"  Passed first time             {work['passed_first']:>3}   {work['passed_first_rate']:>5.1f}%")
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
        lines.append("  run eval/run-corpus.sh, or plain-english baseline for a free estimate")

    opt_out = summary.get("flagged_text_opt_out")
    if isinstance(opt_out, dict) and opt_out.get("active"):
        lines.append("")
        lines.append(f"Note: PLAIN_ENGLISH_LOG_FLAGGED_TEXT=0 was set for {opt_out['missing_events']} of {opt_out['total_events']} events.")
        lines.append("Rule counts are complete. Per-finding text is unavailable for that period.")

    return "\n".join(lines)


def format_report_markdown(summary: dict[str, object]) -> str:
    lines: list[str] = []
    report_date = summary["end_date"]
    start_date = summary["start_date"]
    end_date = summary["end_date"]
    days = summary["days"]
    lint_count = summary["lint_events_count"]
    turn_count = summary["turn_events_count"]

    lines.append(f"# Plain English telemetry — {report_date}")
    lines.append("")
    lines.append(f"Window: {start_date} to {end_date} ({days} days)")
    lines.append(f"Source: ~/.claude/plain-english/events.jsonl ({lint_count} lint events, {turn_count} turn events)")
    lines.append("")

    work = summary["work"]
    assert isinstance(work, dict)
    lines.append("## Work")
    lines.append("")
    lines.append("| Measure | Count | Rate |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Markdown writes seen | {work['total_writes']} | |")
    lines.append(f"| Passed first time | {work['passed_first']} | {work['passed_first_rate']:.1f}% |")
    lines.append(f"| Blocked | {work['blocked']} | {work['blocked_rate']:.1f}% |")
    lines.append(f"| Escaped after 3 attempts | {work['escaped']} | {work['escaped_rate']:.1f}% |")
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
    lines.append(f"Reminder injection is exact: {cost['reminder_turns']:,} turns at 76 words each.")
    lines.append("Re-authoring is estimated from document size and is marked as an estimate.")
    lines.append("")
    lines.append("| Measure | Count | Words | Tokens |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Reminder | {cost['reminder_turns']:,} turns | {cost['reminder_words']:,} words | {_format_token_k(int(cost['reminder_tokens_est']))} input tokens |")
    lines.append(f"| Rework | {cost['rework_rewrites']} rewrites | {cost['rework_words']:,} words | {_format_token_k(int(cost['rework_tokens_est']))} output tokens |")
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


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--hook"]:
        return run_hook(sys.stdin.read())
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
    print("usage: linter.py --hook | --turn", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
