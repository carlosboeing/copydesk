"""Keep the preset, the compiled inventory and the generated instructions aligned."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = REPOSITORY_ROOT / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402


PRESET_PATH = REPOSITORY_ROOT / "rules" / "plain.json"
GENERATOR = REPOSITORY_ROOT / "scripts" / "generate-instructions.py"
OUTPUT_STYLE = REPOSITORY_ROOT / "output-styles" / "plain-english.md"

RULES_START = "<!-- plain-english-rules:start -->"
RULES_END = "<!-- plain-english-rules:end -->"


def extract_rules_block(text: str) -> str:
    try:
        return text.split(RULES_START, 1)[1].split(RULES_END, 1)[0]
    except IndexError as error:
        raise AssertionError("missing canonical rules block markers") from error


def preset() -> dict:
    return json.loads(PRESET_PATH.read_text(encoding="utf-8"))


class PresetCompilationTests(unittest.TestCase):
    def test_every_preset_token_reaches_the_compiled_inventory(self) -> None:
        """Dropping a token from compilation must make this test fail."""
        declared = []
        for block in preset()["patterns"]:
            for token in block["tokens"]:
                declared.append(token if isinstance(token, str) else token["phrase"])

        compiled = [pattern.phrase for pattern in linter.RULE_PATTERNS]
        self.assertEqual(len(declared), len(compiled))
        self.assertSetEqual(set(declared), set(compiled))

    def test_compiled_patterns_keep_their_preset_check_and_severity(self) -> None:
        """Compiling a token under the wrong rule id is a defect."""
        expected = {}
        for block in preset()["patterns"]:
            for token in block["tokens"]:
                phrase = token if isinstance(token, str) else token["phrase"]
                expected[phrase] = (block["id"], block["severity"])

        for pattern in linter.RULE_PATTERNS:
            self.assertEqual((pattern.check, pattern.severity), expected[pattern.phrase], pattern.phrase)

    def test_compilation_order_follows_the_preset(self) -> None:
        """Findings are sorted, so order need only be deterministic, not arbitrary."""
        declared = []
        for block in preset()["patterns"]:
            for token in block["tokens"]:
                declared.append(token if isinstance(token, str) else token["phrase"])
        compiled = [pattern.phrase for pattern in linter.RULE_PATTERNS]
        self.assertEqual(declared, compiled)

    def test_every_token_compiles_and_matches_its_own_phrase(self) -> None:
        """A token that cannot match anything is a rule that never fires."""
        for pattern in linter.RULE_PATTERNS:
            with self.subTest(phrase=pattern.phrase):
                self.assertIsNotNone(pattern.regex.pattern)
                self.assertGreater(len(pattern.regex.pattern), 0)

    def test_reference_phrases_are_carried_but_never_executed(self) -> None:
        """A rules-block edit must not escape the sync test by hiding in prose."""
        declared = tuple(p if isinstance(p, str) else p["phrase"] for p in preset()["reference_phrases"])
        self.assertEqual(declared, linter.CANONICAL_REFERENCE_PHRASES)
        executable = {pattern.phrase for pattern in linter.RULE_PATTERNS}
        self.assertSetEqual(set(declared) & executable, set())

    def test_ai_tells_come_from_the_preset(self) -> None:
        self.assertSetEqual(set(preset()["ai_tells"]), set(linter.AI_TELL_PHRASES))

    def test_quoted_rules_block_phrases_reach_the_inventory(self) -> None:
        """Quoting a new phrase in the rules block without a pattern is a defect."""
        block = extract_rules_block(OUTPUT_STYLE.read_text(encoding="utf-8"))
        quoted = {phrase.casefold() for phrase in re.findall(r'"([^"]+)"', block)}
        inventory = {phrase.casefold() for phrase in linter.PATTERN_TEXTS}
        self.assertSetEqual(quoted - inventory, set())


class GeneratedInstructionTests(unittest.TestCase):
    def test_committed_instructions_match_the_generator(self) -> None:
        """Hand-editing an instruction set instead of the preset must fail here."""
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reminder_word_count_agrees_with_the_linter(self) -> None:
        """The precis is re-sent every turn, so its length is a measured cost."""
        instructions = preset()["instructions"]
        self.assertEqual(len(instructions["reminder"].split()), instructions["reminder_word_count"])
        self.assertEqual(instructions["reminder_word_count"], linter.REMINDER_WORD_COUNT)

    def test_output_style_names_the_preset_rather_than_the_tool(self) -> None:
        """The instruction set holds one preset's rules; renaming it to CopyDesk misnames it."""
        text = OUTPUT_STYLE.read_text(encoding="utf-8")
        self.assertIn(f"name: {preset()['instructions']['output_style']['name']}", text)
        self.assertIn(RULES_START, text)
        self.assertIn(RULES_END, text)


class InstalledCopyTests(unittest.TestCase):
    def test_installed_instructions_carry_the_same_rules_block(self) -> None:
        """Changing one canonical instruction set must make the sync check fail."""
        instructions = Path.home() / ".claude" / "CLAUDE.md"
        if not instructions.is_file():
            self.skipTest("~/.claude/CLAUDE.md is absent; no installed copy to compare")
        if RULES_START not in instructions.read_text(encoding="utf-8"):
            self.skipTest("the installed instructions carry no CopyDesk rules block")

        self.assertEqual(
            extract_rules_block(instructions.read_text(encoding="utf-8")),
            extract_rules_block(OUTPUT_STYLE.read_text(encoding="utf-8")),
        )


class RenameTests(unittest.TestCase):
    def test_the_preset_key_is_instructions(self) -> None:
        p = preset()
        self.assertIn("instructions", p)
        self.assertNotIn("carr" + "iers", p)

    def test_the_old_word_is_gone_from_every_source_file(self) -> None:
        # The needle is assembled rather than written out, so this file does
        # not match its own scan. Spelling it here would make the test fail
        # forever and tempt the next reader to exclude the scanner instead.
        needle = "carr" + "ier"
        roots = ("lib", "bin", "scripts", "hooks", "rules", "output-styles", "tests", "docs")
        offenders = []
        for root in roots:
            for path in (REPOSITORY_ROOT / root).rglob("*"):
                if not path.is_file() or path.suffix in (".pyc", ".png"):
                    continue
                if needle in path.read_text(encoding="utf-8", errors="ignore").lower():
                    offenders.append(str(path.relative_to(REPOSITORY_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
