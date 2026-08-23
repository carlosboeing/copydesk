"""The retry scope: a block must come from the text the model just wrote."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GATE = REPOSITORY_ROOT / "hooks" / "gate.sh"
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import linter  # noqa: E402


BANNED = "The design is robust."
CLEAN = "A short sentence has enough words here."
LONG_ERROR = (
    "This entry carries a pre-existing sentence that runs far past every limit "
    "the gate enforces because it keeps adding clause after clause after clause "
    "without ever reaching a proper stopping point, and it continues past every "
    "natural pause, piling subordinate clause on subordinate clause until the "
    "reader quite loses the thread of the entire thing altogether."
)


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

    # --- issue 8: pre-existing sentences must never carry the block ----------

    def test_an_unchanged_line_inside_old_string_does_not_block(self) -> None:
        """The replacement span includes context lines; identical text is not authored."""
        path = self.write("d2.md", f"- {LONG_ERROR}\n- A short bullet sits below.\n")
        result = self.gate(
            self.edit_payload(
                path,
                f"- {LONG_ERROR}\n- A short bullet sits below.",
                f"- {LONG_ERROR}\n- A short bullet reworded here.",
                session="issue8a",
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_pre_existing_sentence_is_reported_while_a_new_error_blocks(self) -> None:
        """The block names only the new text; the old error is counted, not charged."""
        path = self.write("d6.md", f"- {LONG_ERROR}\n- A short bullet sits below.\n")
        result = self.gate(
            self.edit_payload(
                path,
                f"- {LONG_ERROR}\n- A short bullet sits below.",
                f"- {LONG_ERROR}\n- A short bullet, robust and simple.",
                session="issue8e",
            )
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("banned-word", result.stderr)
        self.assertNotIn(LONG_ERROR[:40], result.stderr)
        self.assertIn("pre-existing error", result.stderr)
        self.assertIn("need no change", result.stderr)

    def test_a_pre_existing_sentence_sharing_the_edited_line_passes(self) -> None:
        """Two sentences share one physical line; editing one must not block the other."""
        path = self.write("d3.md", f"Opening short line here. Second up, {LONG_ERROR[0].lower()}{LONG_ERROR[1:]}\n")
        result = self.gate(
            self.edit_payload(path, "Opening short line here.", "Opening line rewritten.", session="issue8b")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_control_an_error_written_inside_the_edit_still_blocks(self) -> None:
        """A fix that stopped blocking on everything would break the gate here."""
        path = self.write("d4.md", "A clean intro line sits here.\n")
        result = self.gate(
            self.edit_payload(path, "A clean intro line sits here.", f"A clean intro line sits here. {LONG_ERROR}", session="issue8c")
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("sentence-length", result.stderr)

    def test_an_edit_inside_a_pre_existing_long_sentence_blocks(self) -> None:
        """A sentence the edit cuts into belongs to the edit: partly authored prose blocks.

        The sentence starts on line one and the replaced word sits on its second
        line, so anchor-line attribution alone would call it pre-existing.
        """
        words = LONG_ERROR.split()
        cut = len(words) // 2
        path = self.write("d5.md", " ".join(words[:cut]) + "\n" + " ".join(words[cut:]) + "\n")
        target = words[cut + 2]
        result = self.gate(
            self.edit_payload(path, " " + target + " ", " rewoven ", session="issue8d")
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("sentence-length", result.stderr)

    def test_deleting_a_full_stop_that_joins_two_sentences_blocks(self) -> None:
        """A deletion-only inner opcode must still own the join it creates."""
        first = " ".join(["alpha"] * 24) + " here."
        second = "Then " + " ".join(["bravo"] * 24) + "."
        path = self.write("d7.md", first + " " + second + "\n")
        result = self.gate(
            self.edit_payload(path, "here. Then", "here Then", session="issue8f")
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("sentence-length", result.stderr)

    def test_deleting_a_period_line_that_joins_two_sentences_blocks(self) -> None:
        """A line-level delete that joins two sentences owns the join point."""
        first = " ".join(["alpha"] * 24) + " here"
        second = "Then " + " ".join(["bravo"] * 24) + "."
        path = self.write("d8.md", first + "\n.\n" + second + "\n")
        result = self.gate(self.edit_payload(path, "\n.\n", "\n", session="issue8g"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("sentence-length", result.stderr)

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
        self.assertNotIn("pre-existing error", result.stderr)

    def test_a_pre_existing_long_sentence_rate_does_not_block_an_unrelated_edit(self) -> None:
        long_sentence = " ".join(["word"] * 30) + "."
        body = "\n\n".join([long_sentence] * 16 + ["Another line sits here quietly."])
        path = self.write("i.md", body + "\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.", "Another line sits here calmly.", session="e7")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_edit_that_newly_trips_paragraph_length_blocks(self) -> None:
        """An edit that grows a paragraph past the cap owns the result.

        The edit's characters land inside the sentences the rule counts,
        so ordinary overlap charges it. The rule is not on the whole-document
        list and does not take the newly-fires path.
        """
        four = (
            "One short sentence sits here now. Two more words follow along. "
            "Three keeps under the word cap. Four finishes this paragraph cleanly."
        )
        path = self.write("p1.md", four + "\n")
        result = self.gate(
            self.edit_payload(
                path,
                "Four finishes this paragraph cleanly.",
                "Four finishes this paragraph cleanly. Five adds one more sentence here.",
                session="e6p",
            )
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("paragraph-length", result.stderr)
        self.assertNotIn("pre-existing error", result.stderr)

    def test_an_edit_inside_an_over_long_paragraph_blocks(self) -> None:
        # The paragraph was already over the cap. Editing inside it is what
        # the span answers: while paragraph-length skipped origin filtering,
        # one old violation switched the rule off for the whole file.
        over = ("One short line here. Two short lines here. Three short lines here. "
                "Four short lines here. Five short lines here.")
        path = self.write("pl1.md", over + "\n\nA separate clean paragraph sits here.\n")
        result = self.gate(self.edit_payload(
            path, "Three short lines here.", "Three rewritten lines here.", session="pl1"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("paragraph-length", result.stderr)

    def test_deleting_a_paragraph_break_owns_the_merged_paragraph(self) -> None:
        # A deletion is a zero-width point that falls between two counted
        # sentences by definition, so per-sentence ranges alone never see it.
        # Removing text there is exactly what merges two paragraphs into one
        # over the cap.
        doc = ("One short line here. Two short lines here. Three short lines here.\n\n"
               "Four short lines here. Five short lines here.")
        path = self.write("pl5.md", doc + "\n")
        result = self.gate(self.edit_payload(
            path, "here.\n\nFour", "here. Four", session="pl5"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("paragraph-length", result.stderr)

    def test_a_bullet_between_counted_sentences_is_not_blamed(self) -> None:
        # The outer bounds run from the first counted sentence to the last, so
        # a bullet sitting between two of them falls inside them. The finding
        # carries each counted sentence separately for exactly this geometry.
        doc = ("One short line here. Two short lines here.\n"
               "- bullet sits here now\n"
               "Three short lines here. Four short lines here. Five short lines here.")
        path = self.write("pl4.md", doc + "\n")
        result = self.gate(self.edit_payload(
            path, "- bullet sits here now", "- bullet reworded here now", session="pl4"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rewording_a_bullet_is_not_blamed_for_the_intro(self) -> None:
        # List item lines are excluded from the sentence count, so the span
        # stops at the last counted sentence. A span covering the bullets
        # would blame one for prose it was never measured against.
        block = ("One short line here. Two short lines here. Three short lines here. "
                 "Four short lines here. Five short lines here.\n"
                 "- first bullet sits here\n"
                 "- second bullet sits here")
        path = self.write("pl3.md", block + "\n")
        result = self.gate(self.edit_payload(
            path, "- second bullet sits here", "- second bullet reworded here", session="pl3"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_edit_in_another_paragraph_leaves_it_alone(self) -> None:
        over = ("One short line here. Two short lines here. Three short lines here. "
                "Four short lines here. Five short lines here.")
        path = self.write("pl2.md", over + "\n\nA separate clean paragraph sits here.\n")
        result = self.gate(self.edit_payload(
            path, "A separate clean paragraph sits here.",
            "A separate tidy paragraph sits here.", session="pl2"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_pre_existing_paragraph_length_does_not_block_an_unrelated_edit(self) -> None:
        five = (
            "One short sentence sits here now. Two more words follow along. "
            "Three keeps under the word cap. Four finishes this paragraph cleanly. "
            "Five was already over the cap."
        )
        body = five + "\n\nAnother line sits here quietly."
        path = self.write("p2.md", body + "\n")
        result = self.gate(
            self.edit_payload(path, "Another line sits here quietly.", "Another line sits here calmly.", session="e7p")
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

    def test_every_whole_document_metric_is_excluded_from_origin_filtering(self) -> None:
        # A rule measured over the whole document has no text to point at, so
        # origin overlap cannot place it and it would never block an Edit.
        # All four take the newly-fires path instead.
        for check in ("long-sentence-rate", "avg-sentence-length",
                      "sentence-variation", "list-dominated"):
            finding = linter.Finding(1, check, "x", "error", origin="new")
            self.assertEqual(linter.blocking_findings_for_retry([finding]), [], check)
            self.assertIn(check, linter.DOCUMENT_SCOPED_BLOCKING_RULES)

    def test_the_ratio_rule_is_excluded_but_paragraph_length_is_not(self) -> None:
        # paragraph-length has its paragraph's sentences and takes the
        # ordinary path, so a new one blocks like any other finding.
        ratio = linter.Finding(1, "long-sentence-rate", "x", "error", origin="new")
        self.assertEqual(linter.blocking_findings_for_retry([ratio]), [])
        paragraph = linter.Finding(2, "paragraph-length", "y", "error", origin="new")
        self.assertEqual(linter.blocking_findings_for_retry([paragraph]), [paragraph])
        existing = linter.Finding(2, "paragraph-length", "y", "error", origin="existing")
        self.assertEqual(linter.blocking_findings_for_retry([existing]), [])

    def test_inner_narrowing_excludes_unchanged_context(self) -> None:
        existing = "unchanged prefix XXX unchanged suffix"
        proposed = "unchanged prefix YYY unchanged suffix"
        ranges = linter._changed_char_ranges(existing, proposed)
        self.assertEqual(len(ranges), 1)
        lo, hi = ranges[0]
        self.assertGreaterEqual(lo, proposed.index("YYY"))
        self.assertLessEqual(hi, proposed.index("YYY") + 3)

    def test_a_long_document_pair_skips_the_line_matcher(self) -> None:
        # The outer matcher is quadratic in line count. Past the hook's
        # timeout the gate is killed and every write goes through unchecked,
        # so a document pair over the cap is charged whole instead.
        paragraphs = "\n\n".join(
            f"Paragraph {i} sits here with a few ordinary words." for i in range(1600)
        )
        changed = paragraphs.replace("ordinary", "different")
        started = time.time()
        with self.assertRaises(linter._TooLargeToCompare):
            linter._changed_char_ranges(paragraphs, changed)
        self.assertLess(time.time() - started, 2.0, "the line matcher ran uncapped")

    def test_a_long_document_charges_only_the_edit(self) -> None:
        # Charging the whole document past the cap refused every pre-existing
        # error in the file, which is the symptom this attribution removes.
        paragraphs = "\n\n".join(
            f"Paragraph {i} sits here with a few ordinary words." for i in range(1600)
        )
        old = "Paragraph 800 sits here with a few ordinary words."
        new = "Paragraph 800 sits here with several different words."
        proposed = paragraphs.replace(old, new)
        findings = [
            linter.Finding(1, "banned-word", "x", "error", span_start=0, span_end=5),
            linter.Finding(2, "banned-word", "y", "error",
                           span_start=paragraphs.find(old), span_end=paragraphs.find(old) + 10),
        ]
        placed = linter._compute_edit_origins(findings, paragraphs, old, proposed)
        self.assertEqual(placed[0].origin, "existing", "a far-away finding was charged")
        self.assertEqual(placed[1].origin, "new", "the edited text was not charged")

    def test_a_large_replaced_block_falls_back_without_stalling(self) -> None:
        n = 20000
        existing = "a" * n
        proposed = "b" * n
        start = time.perf_counter()
        ranges = linter._changed_char_ranges(existing, proposed)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)
        self.assertEqual(ranges, [(0, n)])


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        old_state = os.environ.get("COPYDESK_STATE_DIR")
        os.environ["COPYDESK_STATE_DIR"] = str(self.root / "state")

        def _restore():
            if old_state is None:
                os.environ.pop("COPYDESK_STATE_DIR", None)
            else:
                os.environ["COPYDESK_STATE_DIR"] = old_state

        self.addCleanup(_restore)
        (self.root / "copydesk.config.json").write_text(
            '{"version": 1, "paths": {"ignore": ["notes/**"], "warn": ["CHANGELOG.md"]}}',
            encoding="utf-8",
        )
        (self.root / "notes").mkdir()
        linter._PRESET_CACHE.clear()

    def _hook(self, name: str) -> int:
        target = self.root / name
        payload = json.dumps({
            "session_id": "routing", "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "This approach is robust.\n"},
        })
        return linter.run_hook(payload)

    def test_an_ignored_path_never_reaches_the_gate(self) -> None:
        self.assertEqual(self._hook("notes/x.md"), 0)

    def test_a_warned_path_reports_and_lets_the_write_through(self) -> None:
        self.assertEqual(self._hook("CHANGELOG.md"), 0)

    def test_a_warned_path_records_its_decision(self) -> None:
        self._hook("CHANGELOG.md")
        events = linter.read_events()
        self.assertEqual(events[-1]["decision"], "warn")

    def test_a_warned_path_starts_no_retry_streak(self) -> None:
        self._hook("CHANGELOG.md")
        self._hook("CHANGELOG.md")
        self.assertEqual(linter.read_events()[-1]["streak"], 0)

    def test_a_blocked_path_still_blocks(self) -> None:
        self.assertEqual(self._hook("README.md"), 2)

    def test_the_cli_skips_an_ignored_path_out_loud(self) -> None:
        (self.root / "notes" / "x.md").write_text("This approach is robust.\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "bin" / "copydesk"), "check", "notes/x.md"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("not checked", result.stdout)
        self.assertNotIn("banned-word", result.stdout)


class ConfigFailOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.doc = self.root / "doc.md"
        self.doc.write_text("This approach is robust and comprehensive.\n", encoding="utf-8")
        self.state = self.root / "state"
        linter._PRESET_CACHE.clear()

    def _check(self) -> subprocess.CompletedProcess:
        env = dict(os.environ, COPYDESK_STATE_DIR=str(self.state))
        return subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "bin" / "copydesk"), "check", "doc.md"],
            cwd=self.root, capture_output=True, text=True, env=env,
        )

    def _hook(self) -> int:
        payload = json.dumps({
            "session_id": "fail-open",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.doc),
                "content": "This approach is robust and comprehensive.\n",
            },
        })
        env = dict(os.environ, COPYDESK_STATE_DIR=str(self.state))
        return subprocess.run(
            ["bash", str(GATE)], input=payload, text=True, capture_output=True, env=env, cwd=self.root
        ).returncode

    def test_control_document_without_config_reports_findings_and_exits_one(self) -> None:
        result = self._check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("banned-word", result.stdout)

    def test_malformed_config_reports_error_and_lints_with_exit_one(self) -> None:
        (self.root / "copydesk.config.json").write_text("{ not json", encoding="utf-8")
        result = self._check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not valid JSON", result.stderr)
        self.assertIn("banned-word", result.stdout)

    def test_invalid_severity_reports_error_and_lints_with_exit_one(self) -> None:
        (self.root / "copydesk.config.json").write_text(
            '{"version": 1, "rules": {"banned-word": {"severity": "invalid"}}}', encoding="utf-8"
        )
        result = self._check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("rules.banned-word.severity", result.stderr)
        self.assertIn("banned-word", result.stdout)

    def test_gate_hook_with_malformed_config_still_evaluates_and_blocks(self) -> None:
        (self.root / "copydesk.config.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(self._hook(), 2)


if __name__ == "__main__":
    unittest.main()
