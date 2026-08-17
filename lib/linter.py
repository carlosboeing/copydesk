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


@dataclass(frozen=True)
class Finding:
    """One line-oriented check result for the command or hook."""

    line: int
    check: str
    excerpt: str
    severity: str

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
        file_path, proposed, session_id = _proposed_document(payload)
        if file_path is None or proposed is None or session_id is None:
            return 0

        findings = lint(proposed, path=file_path)
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
            return 0

        files[file_path] = {
            "content_hash": content_hash,
            "hashes": hashes,
            "streak": streak,
            "updated_at": now,
        }
        _write_state(state_path, state)
        for finding in findings:
            if finding.severity == "error":
                print(finding.render(), file=sys.stderr)
        if has_ai_tell_finding(proposed, findings):
            print("Run /humanizer for the AI-tell failures before retrying.", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError):
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--hook"]:
        return run_hook(sys.stdin.read())
    print("usage: linter.py --hook", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
