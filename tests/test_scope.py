"""The retry scope: a block must come from the text the model just wrote."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GATE = REPOSITORY_ROOT / "hooks" / "gate.sh"
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import linter  # noqa: E402


BANNED = "The design is robust."
CLEAN = "A short sentence has enough words here."


class GateScopeTests(unittest.TestCase):
    """Drives the real hook, so the decision and the exit code are both covered."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.addCleanup(self.temp.cleanup)

    def gate(self, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ, COPYDESK_STATE_DIR=str(self.state))
        return subprocess.run(
            ["bash", str(GATE)], input=json.dumps(payload), text=True, capture_output=True, env=env
        )

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def edit_payload(self, path: Path, old: str, new: str, *, session: str, replace_all: bool = False) -> dict:
        return {
            "session_id": session,
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(path),
                "old_string": old,
                "new_string": new,
                "replace_all": replace_all,
            },
        }

    # --- Write is unchanged --------------------------------------------------

    def test_a_clean_write_passes(self) -> None:
        result = self.gate({
            "session_id": "w1", "tool_name": "Write",
            "tool_input": {"file_path": str(self.root / "a.md"), "content": CLEAN + "\n"},
        })
        self.assertEqual(result.returncode, 0)

    def test_a_write_carrying_an_error_still_blocks(self) -> None:
        """Every Write finding is marked new, so Write behaviour must not move."""
        result = self.gate({
            "session_id": "w2", "tool_name": "Write",
            "tool_input": {"file_path": str(self.root / "b.md"), "content": BANNED + "\n"},
        })
        self.assertEqual(result.returncode, 2)
        self.assertIn("banned-word", result.stderr)

    # --- Edit is narrowed ----------------------------------------------------

    def test_an_edit_inserting_a_banned_word_blocks(self) -> None:
        path = self.write("c.md", CLEAN + "\n")
        result = self.gate(self.edit_payload(path, CLEAN, CLEAN + "\n\n" + BANNED, session="e1"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("banned-word", result.stderr)

    def test_an_edit_beside_a_pre_existing_banned_word_passes(self) -> None:
        """205 of 265 files here blocked an edit, and none came from the new text."""
        path = self.write("d.md", BANNED + "\n\nAnother line sits here quietly.\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.", "Another line sits here calmly.", session="e2")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_pure_deletion_passes(self) -> None:
        path = self.write("e.md", BANNED + "\n\nAnother line sits here quietly.\n")
        result = self.gate(
            self.edit_payload(path, "\n\nAnother line sits here quietly.", "", session="e3")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replace_all_attributes_across_accumulating_line_shifts(self) -> None:
        """Each replacement shifts the ones after it, so the ranges must accumulate."""
        body = "\n\n".join([BANNED, "repeat me", "filler line one.", "repeat me", "filler line two.", "repeat me"])
        path = self.write("f.md", body + "\n")
        result = self.gate(
            self.edit_payload(path, "repeat me", "repeat me now", session="e4", replace_all=True)
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replace_all_still_blocks_when_the_replacement_carries_an_error(self) -> None:
        body = "\n\n".join(["repeat me", "filler line one.", "repeat me", "filler line two.", "repeat me"])
        path = self.write("g.md", body + "\n")
        result = self.gate(
            self.edit_payload(path, "repeat me", "repeat me, robustly robust", session="e5", replace_all=True)
        )
        self.assertEqual(result.returncode, 2)

    # --- the document-scoped rule -------------------------------------------

    def test_an_edit_that_newly_trips_long_sentence_rate_blocks(self) -> None:
        """The one blocking rule origin filtering cannot place."""
        short = " ".join(["Short line here."] * 1)
        clean_body = "\n\n".join([f"Sentence number {n} stays well under the limit." for n in range(20)])
        path = self.write("h.md", clean_body + "\n")
        long_sentence = " ".join(["word"] * 30) + "."
        addition = "\n\n".join(long_sentence for _ in range(12))
        result = self.gate(
            self.edit_payload(path, "Sentence number 0 stays well under the limit.",
                              "Sentence number 0 stays well under the limit.\n\n" + addition, session="e6")
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("long-sentence-rate", result.stderr)

    def test_a_pre_existing_long_sentence_rate_does_not_block_an_unrelated_edit(self) -> None:
        long_sentence = " ".join(["word"] * 30) + "."
        body = "\n\n".join([long_sentence] * 16 + ["Another line sits here quietly."])
        path = self.write("i.md", body + "\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.", "Another line sits here calmly.", session="e7")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- what the model is told ---------------------------------------------

    def test_only_the_blocking_findings_are_printed(self) -> None:
        """Asking the model to fix text it did not write is what made refusal obstructive."""
        path = self.write("j.md", "The first design is robust.\n\nAnother line sits here quietly.\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.",
                              "Another line sits here quietly.\n\nThe second plan is comprehensive.", session="e8")
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("comprehensive", result.stderr)
        self.assertNotIn("The first design is robust", result.stderr)

    def test_the_pre_existing_count_is_reported_without_asking_for_a_fix(self) -> None:
        path = self.write("k.md", "The first design is robust.\n\nAnother line sits here quietly.\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.",
                              "Another line sits here quietly.\n\nThe second plan is comprehensive.", session="e9")
        )
        self.assertIn("pre-existing error", result.stderr)
        self.assertIn("need no change", result.stderr)

    # --- unchanged behaviour -------------------------------------------------

    def test_the_three_strike_escape_is_unchanged(self) -> None:
        """Two blocks, then a pass with a recorded warning."""
        exits = []
        for _ in range(3):
            exits.append(self.gate({
                "session_id": "esc", "tool_name": "Write",
                "tool_input": {"file_path": str(self.root / "l.md"), "content": BANNED + "\n"},
            }).returncode)
        self.assertEqual(exits, [2, 2, 0])

    def test_the_telemetry_event_schema_is_unchanged(self) -> None:
        """Origin tracking and rollups already record what the new decision reads."""
        path = self.write("m.md", BANNED + "\n\nAnother line sits here quietly.\n")
        self.gate(self.edit_payload(path, "Another line sits here quietly.",
                                    "Another line sits here calmly.", session="tel"))
        events = [json.loads(line) for line in (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        lint_events = [e for e in events if e.get("event") == "lint"]
        self.assertTrue(lint_events)
        for key in ("origin_totals", "rule_totals", "blocking_origin_totals", "blocking_rule_totals", "findings", "decision"):
            self.assertIn(key, lint_events[-1])
        self.assertEqual(lint_events[-1]["decision"], "pass")


class DecisionHelperTests(unittest.TestCase):
    def test_only_new_origin_errors_are_blocking(self) -> None:
        findings = [
            linter.Finding(1, "banned-word", "x", "error", origin="existing"),
            linter.Finding(2, "banned-word", "y", "error", origin="new"),
            linter.Finding(3, "verb-jargon", "z", "warning", origin="new"),
        ]
        blocking = linter.blocking_findings_for_retry(findings)
        self.assertEqual([f.line for f in blocking], [2])

    def test_the_document_scoped_rule_is_excluded_from_origin_filtering(self) -> None:
        findings = [linter.Finding(1, "long-sentence-rate", "x", "error", origin="new")]
        self.assertEqual(linter.blocking_findings_for_retry(findings), [])
        self.assertIn("long-sentence-rate", linter.DOCUMENT_SCOPED_BLOCKING_RULES)


if __name__ == "__main__":
    unittest.main()
