"""Keep the canonical rules and the linter's mechanical vocabulary aligned."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = REPOSITORY_ROOT / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402


CANONICAL_DESIGN = REPOSITORY_ROOT / "docs" / "2-design" / "2026-08-16-copydesk-v2-design.md"


def extract_rules_block(text: str) -> str:
    start = "<!-- plain-english-rules:start -->"
    end = "<!-- plain-english-rules:end -->"
    try:
        return text.split(start, 1)[1].split(end, 1)[0]
    except IndexError as error:
        raise AssertionError("missing canonical rules block markers") from error


def quoted_phrases_in_approved_rules() -> set[str]:
    """Read Decision 2 until Task 3 creates the two runtime carriers."""
    lines: list[str] = []
    started = False
    for line in CANONICAL_DESIGN.read_text(encoding="utf-8").splitlines():
        if line == "> **Answer first, then support it.**":
            started = True
        if not started:
            continue
        if not line.startswith(">"):
            break
        lines.append(line[1:].lstrip())
    if not lines:
        raise AssertionError("Decision 2 canonical rules block is missing")
    return {phrase.casefold() for phrase in re.findall(r'"([^"]+)"', "\n".join(lines))}


class RulesSyncTests(unittest.TestCase):
    def test_linter_inventory_covers_every_quoted_canonical_phrase(self) -> None:
        """Removing a rules-block phrase from the pattern inventory is a defect."""
        pattern_texts = {phrase.casefold() for phrase in getattr(linter, "PATTERN_TEXTS", ())}
        self.assertSetEqual(quoted_phrases_in_approved_rules() - pattern_texts, set())

    def test_prose_copies_are_byte_identical_when_task_three_installs_them(self) -> None:
        """Changing one canonical carrier must make the sync check fail."""
        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        if not claude_md.is_file():
            self.skipTest("~/.claude/CLAUDE.md is absent; no installed copy to compare")

        output_style = REPOSITORY_ROOT / "output-styles" / "plain-english.md"
        if not output_style.is_file():
            self.skipTest("Task 3 has not moved the canonical output style into the bundle")

        self.assertEqual(
            extract_rules_block(claude_md.read_text(encoding="utf-8")),
            extract_rules_block(output_style.read_text(encoding="utf-8")),
        )


if __name__ == "__main__":
    unittest.main()
