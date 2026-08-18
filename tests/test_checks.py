"""Behavior tests for the Plain English CLI and PreToolUse wrapper."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CLI = REPOSITORY_ROOT / "tools" / "plain-english" / "bin" / "plain-english"
GATE = REPOSITORY_ROOT / "tools" / "plain-english" / "hooks" / "gate.sh"
INSTALLER = REPOSITORY_ROOT / "tools" / "plain-english" / "install.sh"
FIXTURES = Path(__file__).parent / "fixtures"

_MODULE_TEMP_DIR: tempfile.TemporaryDirectory | None = None
_ORIG_STATE_DIR: str | None = None


def setUpModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    _MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
    _ORIG_STATE_DIR = os.environ.get("PLAIN_ENGLISH_STATE_DIR")
    os.environ["PLAIN_ENGLISH_STATE_DIR"] = _MODULE_TEMP_DIR.name


def tearDownModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    if _ORIG_STATE_DIR is not None:
        os.environ["PLAIN_ENGLISH_STATE_DIR"] = _ORIG_STATE_DIR
    else:
        os.environ.pop("PLAIN_ENGLISH_STATE_DIR", None)
    if _MODULE_TEMP_DIR is not None:
        _MODULE_TEMP_DIR.cleanup()
        _MODULE_TEMP_DIR = None


class PlainEnglishCliTests(unittest.TestCase):
    def run_fixture(self, name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if "PLAIN_ENGLISH_STATE_DIR" not in env and _MODULE_TEMP_DIR is not None:
            env["PLAIN_ENGLISH_STATE_DIR"] = _MODULE_TEMP_DIR.name
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
            installed = bin_dir / "plain-english"
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
                ["plain-english", str(FIXTURES / "good.md")],
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
        environment["PLAIN_ENGLISH_STATE_DIR"] = state_dir
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
                "file_path": "/tmp/plain-english-write.md",
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
                "file_path": "/tmp/plain-english-retry.md",
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
