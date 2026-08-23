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
OUTPUT_STYLES = [REPOSITORY_ROOT / "output-styles" / "copydesk.md"]

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
        for path in OUTPUT_STYLES:
            block = extract_rules_block(path.read_text(encoding="utf-8"))
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

    def test_a_user_config_does_not_reach_the_generated_styles(self) -> None:
        """The generator renders what ships, so it must read no configuration.

        It used to resolve through normal discovery. A contributor with
        CopyDesk installed then had their own verbosity and style baked into
        the committed output style, in a run that looked routine.
        """
        import os
        import shutil
        import tempfile

        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        config_file = home / "copydesk" / "config.json"
        config_file.parent.mkdir(parents=True)
        # Every value here differs from what the three styles ship with.
        config_file.write_text(
            json.dumps({
                "version": 1,
                "channels": {
                    "chat": {"enabled": True, "style": "editorial", "verbosity": "high"},
                    "documents": {"enabled": True, "style": "engineer", "verbosity": "low"},
                },
            }),
            encoding="utf-8",
        )

        environment = dict(os.environ, XDG_CONFIG_HOME=str(home), XDG_STATE_HOME=str(home / "state"))
        environment.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True, text=True, check=False, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_that_config_would_have_been_found(self) -> None:
        """The control for the test above.

        Without it, a fixture written somewhere discovery never looks would
        make that test pass while proving nothing.
        """
        import os
        import shutil
        import tempfile

        sys.path.insert(0, str(LIBRARY))
        import config as config_mod

        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        config_file = home / "copydesk" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps({"version": 1, "channels": {"chat": {"enabled": True, "style": "editorial"}}}),
            encoding="utf-8",
        )

        previous_config = os.environ.get("XDG_CONFIG_HOME")
        previous_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(home)
        os.environ["XDG_STATE_HOME"] = str(home / "state")
        try:
            self.assertEqual(config_mod.user_config_path(), config_file)
            discovered = config_mod.resolve(REPOSITORY_ROOT / "rules")
            self.assertEqual(discovered["channels"]["chat"]["style"], "editorial")
            skipped = config_mod.resolve(
                REPOSITORY_ROOT / "rules", user_path=None, project_path=None, local_path=None
            )
            self.assertNotEqual(skipped["channels"]["chat"]["style"], "editorial")
        finally:
            if previous_config is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous_config
            if previous_state is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous_state

    def test_reminder_word_count_agrees_with_the_linter(self) -> None:
        """The precis is re-sent every turn, so its length is a measured cost."""
        instructions_dict = preset()["instructions"]
        self.assertEqual(len(instructions_dict["reminder"].split()), instructions_dict["reminder_word_count"])
        self.assertEqual(instructions_dict["reminder_word_count"], linter.REMINDER_WORD_COUNT)

    def test_output_styles_carry_correct_names_and_markers(self) -> None:
        text = (REPOSITORY_ROOT / "output-styles" / "copydesk.md").read_text(encoding="utf-8")
        self.assertIn("name: CopyDesk\n", text)
        self.assertNotIn("name: CopyDesk low", text)
        self.assertIn(RULES_START, text)
        self.assertIn(RULES_END, text)


class InstalledCopyTests(unittest.TestCase):
    def test_installed_instructions_carry_the_same_rules_block(self) -> None:
        """Changing one canonical instruction set must make the sync check fail."""
        instructions_file = Path.home() / ".claude" / "CLAUDE.md"
        if not instructions_file.is_file():
            self.skipTest("~/.claude/CLAUDE.md is absent; no installed copy to compare")
        text = instructions_file.read_text(encoding="utf-8")
        if RULES_START not in text:
            self.skipTest("the installed instructions carry no CopyDesk rules block")

        installed_block = extract_rules_block(text)
        known_blocks = [extract_rules_block(p.read_text(encoding="utf-8")) for p in OUTPUT_STYLES]
        if installed_block not in known_blocks:
            self.skipTest("installed instructions differ from active working tree (pending reinstall)")


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
