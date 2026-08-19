"""Behavior tests for the CopyDesk CLI and PreToolUse wrapper."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import linter  # noqa: E402
CLI = REPOSITORY_ROOT / "bin" / "copydesk"
GATE = REPOSITORY_ROOT / "hooks" / "gate.sh"
INSTALLER = REPOSITORY_ROOT / "install.sh"
FIXTURES = Path(__file__).parent / "fixtures"

_MODULE_TEMP_DIR: tempfile.TemporaryDirectory | None = None
_ORIG_STATE_DIR: str | None = None


def setUpModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    _MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
    _ORIG_STATE_DIR = os.environ.get("COPYDESK_STATE_DIR")
    os.environ["COPYDESK_STATE_DIR"] = _MODULE_TEMP_DIR.name


def tearDownModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    if _ORIG_STATE_DIR is not None:
        os.environ["COPYDESK_STATE_DIR"] = _ORIG_STATE_DIR
    else:
        os.environ.pop("COPYDESK_STATE_DIR", None)
    if _MODULE_TEMP_DIR is not None:
        _MODULE_TEMP_DIR.cleanup()
        _MODULE_TEMP_DIR = None


class PlainEnglishCliTests(unittest.TestCase):
    def run_fixture(self, name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if "COPYDESK_STATE_DIR" not in env and _MODULE_TEMP_DIR is not None:
            env["COPYDESK_STATE_DIR"] = _MODULE_TEMP_DIR.name
        return subprocess.run(
            [sys.executable, str(CLI), str(FIXTURES / name)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def assert_blocking_check(self, fixture: str, check: str) -> None:
        """Removing this check must make its fixture test fail."""
        result = self.run_fixture(fixture)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, rf"(?m)^\d+:{re.escape(check)}:")

    def test_blocks_a_sentence_over_forty_words_with_a_line_number(self) -> None:
        """Removing the hard sentence cap must make this test fail."""
        result = self.run_fixture("sentence-length.md")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^1:sentence-length:")

    def test_blocks_each_remaining_mechanical_check(self) -> None:
        cases = {
            "long-sentence-rate.md": "long-sentence-rate",
            "paragraph-length.md": "paragraph-length",
            "orphan-pointer.md": "orphan-pointer",
            "soft-offer.md": "soft-offer",
            "banned-word.md": "banned-word",
            "contrast-construction.md": "contrast-construction",
            "announcing-opener.md": "announcing-opener",
            "idiom.md": "idiom",
            "nested-table.md": "nested-table",
        }
        for fixture, check in cases.items():
            with self.subTest(fixture=fixture):
                self.assert_blocking_check(fixture, check)

    def test_reports_advisory_checks_without_blocking(self) -> None:
        cases = {
            "sentence-length-warning.md": "sentence-length",
            "verb-jargon.md": "verb-jargon",
            "avg-sentence-length.md": "avg-sentence-length",
            "sentence-variation.md": "sentence-variation",
        }
        for fixture, check in cases.items():
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, rf"(?m)^\d+:{re.escape(check)}:")

    def test_stays_silent_for_a_known_good_document(self) -> None:
        """Adding a false-positive rule must make this test fail."""
        result = self.run_fixture("good.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_excludes_non_authored_markdown_content(self) -> None:
        fixtures = [
            "frontmatter.md",
            "fenced-code.md",
            "table.md",
            "quotation.md",
            "link-url.md",
            "heading.md",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_exempt_document_types_skip_file_level_statistics_only(self) -> None:
        for fixture in ("roadmap.md", "list-dominated.md"):
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

        self.assert_blocking_check("roadmap-banned-word.md", "banned-word")

    def test_reads_markdown_from_standard_input(self) -> None:
        """Removing stdin support must make pipeline callers fail."""
        result = subprocess.run(
            [sys.executable, str(CLI), "-"],
            input=(FIXTURES / "good.md").read_text(encoding="utf-8"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


class PlainEnglishInstallTests(unittest.TestCase):
    def test_checkout_installer_places_a_working_cli_on_path(self) -> None:
        """Installing from a checkout makes the stable skill command work on PATH."""
        self.assertTrue(INSTALLER.is_file(), "checkout installer is missing")

        with tempfile.TemporaryDirectory() as temporary:
            bin_dir = Path(temporary) / "bin"
            installed = bin_dir / "copydesk"
            result = subprocess.run(
                ["/usr/bin/env", "bash", str(INSTALLER), "--yes", "--bin-dir", str(bin_dir)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(installed.is_symlink())
            self.assertEqual(installed.resolve(), CLI.resolve())

            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
            invocation = subprocess.run(
                ["copydesk", str(FIXTURES / "good.md")],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertEqual(invocation.stdout, "")


class PlainEnglishGateTests(unittest.TestCase):
    def run_gate(self, payload: dict[str, object], state_dir: str) -> subprocess.CompletedProcess[str]:
        """Run the real shell wrapper with isolated retry-state storage."""
        environment = os.environ.copy()
        environment["COPYDESK_STATE_DIR"] = state_dir
        return subprocess.run(
            ["/usr/bin/env", "bash", str(GATE)],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_blocks_a_bad_markdown_write_and_suggests_humanizer_for_ai_tells(self) -> None:
        """Removing Write reconstruction or the blocking exit must fail this test."""
        payload = {
            "session_id": "write-test",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/copydesk-write.md",
                "content": "Delve into the report before approving the release.",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(payload, state_dir)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"(?m)^1:banned-word:")
        self.assertIn("/humanizer", result.stderr)

    def test_reconstructs_an_edit_before_linting(self) -> None:
        """Linting only new_string would let this resulting document bypass the gate."""
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "document.md"
            document.write_text("The report has a clear next action.\n", encoding="utf-8")
            payload = {
                "session_id": "edit-test",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(document),
                    "old_string": "clear",
                    "new_string": "robust",
                    "replace_all": False,
                },
            }
            result = self.run_gate(payload, str(Path(temporary) / "state"))

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"(?m)^1:banned-word:")

    def test_fails_open_for_unrelated_or_malformed_payloads(self) -> None:
        """A missing Write path must never stop an unrelated tool call."""
        payloads = [
            {"session_id": "other", "tool_name": "Read", "tool_input": {"file_path": "/tmp/a.md"}},
            {"session_id": "missing", "tool_name": "Write", "tool_input": {"content": "robust"}},
            {"session_id": "text", "tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "robust"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.md", "content": 42}},
        ]
        with tempfile.TemporaryDirectory() as state_dir:
            for payload in payloads:
                with self.subTest(payload=payload):
                    result = self.run_gate(payload, state_dir)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_allows_the_third_unresolved_attempt_with_a_content_hash_warning(self) -> None:
        """Resetting a failure streak on each revision would make this loop unbounded."""
        payload = {
            "session_id": "retry-test",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/copydesk-retry.md",
                "content": "The robust release needs a clearer description.",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            first = self.run_gate(payload, state_dir)
            second = self.run_gate(payload, state_dir)
            third = self.run_gate(payload, state_dir)

        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn("same content submitted 3 times", third.stderr)
        self.assertIn("sha256=", third.stderr)


if __name__ == "__main__":
    unittest.main()

class UnglossedTermTests(unittest.TestCase):
    """The rule orphan-pointer cannot express: a term used before it is defined."""

    def lint_text(self, text: str, path=None):
        return [f for f in linter.lint(text, path=path) if f.check == "unglossed-term"]

    def test_an_unglossed_first_use_is_flagged(self) -> None:
        found = self.lint_text("The team moved scheduling onto Kubernetes last spring.")
        self.assertEqual([f.excerpt.split(" \u2014")[0] for f in found], ["Kubernetes"])

    def test_the_rule_ships_as_a_warning_and_never_blocks(self) -> None:
        """No learning ships at 0.1.0, so the rule must not refuse a write."""
        found = self.lint_text("The team moved scheduling onto Kubernetes last spring.")
        self.assertTrue(found)
        for finding in found:
            self.assertEqual(finding.severity, "warning")

    def test_each_gloss_form_suppresses_the_finding(self) -> None:
        cases = {
            "appositive": "The team adopted Kubernetes, a container orchestrator, last spring.",
            "parenthetical": "The team adopted Kubernetes (a container orchestrator) last spring.",
            "definition": "The tool they picked, Kubernetes, is a container orchestrator.",
        }
        for form, text in cases.items():
            with self.subTest(gloss=form):
                self.assertEqual(self.lint_text(text), [])

    def test_a_sentence_initial_capital_is_not_a_term(self) -> None:
        self.assertEqual(self.lint_text("Kubernetes was adopted by the team last spring."), [])

    def test_a_list_item_initial_capital_is_sentence_initial(self) -> None:
        """The 2026-08-19 scan flagged Step, Modify, Create, Run and Add as terms."""
        text = "- Step through the queue.\n- Modify the record.\n- Create the namespace.\n- Run the batch.\n- Add the label.\n"
        self.assertEqual(self.lint_text(text), [])

    def test_a_capital_after_a_bold_marker_is_sentence_initial(self) -> None:
        """_SENTENCE_SPLIT does not split across a bold marker, so position zero lies."""
        self.assertEqual(self.lint_text("**Answer first.** **This** is the shape."), [])

    def test_the_masking_sentinels_are_never_terms(self) -> None:
        """exclude_markdown emits CODESPAN and URL, and both look exactly like terms."""
        text = "The team ran `Kubernetes` from https://example.com/runbook every morning.\n"
        found = {f.excerpt.split(" \u2014")[0] for f in self.lint_text(text)}
        self.assertNotIn("CODESPAN", found)
        self.assertNotIn("URL", found)

    def test_code_links_and_headings_are_skipped(self) -> None:
        for label, text in {
            "code": "The team adopted `Kubernetes` for scheduling.",
            "link": "The team adopted [it](https://Kubernetes.io) for scheduling.",
            "heading": "# The team adopted Kubernetes",
        }.items():
            with self.subTest(location=label):
                self.assertEqual(self.lint_text(text), [])

    def test_a_second_use_after_a_glossed_first_use_passes(self) -> None:
        text = (
            "The team adopted Kubernetes (a container orchestrator) last spring.\n"
            "The Kubernetes cluster grew to forty nodes by autumn.\n"
        )
        self.assertEqual(self.lint_text(text), [])

    def test_a_sentence_initial_first_use_counts_as_the_first_use(self) -> None:
        """Otherwise the term's next mid-sentence use is flagged after the reader met it."""
        text = (
            "Kubernetes is a container orchestrator the team adopted.\n"
            "The Kubernetes cluster grew to forty nodes by autumn.\n"
        )
        self.assertEqual(self.lint_text(text), [])

    def test_a_single_letter_is_never_a_term(self) -> None:
        """The pronoun I, a list label, and an initial cannot carry a gloss."""
        found = {f.excerpt.split(" \u2014")[0] for f in self.lint_text("The plan says I should pick option B here.")}
        self.assertNotIn("I", found)
        self.assertNotIn("B", found)

    def test_the_shipped_vocabulary_suppresses_universal_terms(self) -> None:
        text = "The team stored the JSON in Postgres and served it over HTTP from macOS.\n"
        self.assertEqual(self.lint_text(text), [])

    def test_a_project_vocabulary_suppresses_project_terms(self) -> None:
        """CrossRev needs no gloss inside its own repository and needs one in a blog post."""
        text = "The team wired CopyDesk into CrossRev during the migration.\n"
        without = self.lint_text(text)
        self.assertTrue(without, "with no project config both terms are unknown")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "copydesk.config.json").write_text(
                json.dumps({
                    "version": 1,
                    "rules": {"unglossed-term": {"vocabulary": {"add": ["CopyDesk", "CrossRev"]}}},
                }),
                encoding="utf-8",
            )
            document = root / "note.md"
            document.write_text(text, encoding="utf-8")
            linter._PRESET_CACHE.clear()
            self.assertEqual(self.lint_text(text, path=document), [])
        linter._PRESET_CACHE.clear()

    def test_severity_off_silences_the_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "copydesk.config.json").write_text(
                json.dumps({"version": 1, "rules": {"unglossed-term": {"severity": "off"}}}),
                encoding="utf-8",
            )
            document = root / "note.md"
            document.write_text("The team moved onto Kubernetes.\n", encoding="utf-8")
            linter._PRESET_CACHE.clear()
            self.assertEqual(self.lint_text(document.read_text(encoding="utf-8"), path=document), [])
        linter._PRESET_CACHE.clear()

    def test_the_rule_is_recorded_as_a_rule_never_as_a_pattern(self) -> None:
        """Recording it as a token list would mislead a port into treating a heuristic as data."""
        preset = json.loads((REPOSITORY_ROOT / "rules" / "plain-english.json").read_text(encoding="utf-8"))
        self.assertIn("unglossed-term", preset["rules"])
        self.assertEqual([b for b in preset["patterns"] if b["id"] == "unglossed-term"], [])

    def test_every_shipped_term_carries_a_recorded_reason(self) -> None:
        """A reader must be able to challenge one entry rather than the whole list."""
        preset = json.loads((REPOSITORY_ROOT / "rules" / "plain-english.json").read_text(encoding="utf-8"))
        vocabulary = preset["rules"]["unglossed-term"]["vocabulary"]
        explained = {term for group in vocabulary["rationale"].values() for term in group}
        self.assertSetEqual(set(vocabulary["add"]), explained)
