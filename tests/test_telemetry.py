from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "tools" / "plain-english" / "lib"
BIN_PATH = REPO_ROOT / "tools" / "plain-english" / "bin" / "plain-english"
HOOK_DIR = REPO_ROOT / "tools" / "plain-english" / "hooks"
FIXTURES_DIR = REPO_ROOT / "tools" / "plain-english" / "tests" / "fixtures"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import linter


class TestTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.orig_state_dir = os.environ.get("PLAIN_ENGLISH_STATE_DIR")
        os.environ["PLAIN_ENGLISH_STATE_DIR"] = str(self.state_dir)

    def tearDown(self) -> None:
        if self.orig_state_dir is not None:
            os.environ["PLAIN_ENGLISH_STATE_DIR"] = self.orig_state_dir
        else:
            os.environ.pop("PLAIN_ENGLISH_STATE_DIR", None)
        self.temp_dir.cleanup()

    def test_gate_output_equivalence_against_baseline_fixtures(self) -> None:
        fixture_file = FIXTURES_DIR / "gate_baseline.json"
        self.assertTrue(fixture_file.is_file(), "gate_baseline.json fixture must exist")
        fixtures = json.loads(fixture_file.read_text(encoding="utf-8"))

        gate_sh = HOOK_DIR / "gate.sh"

        def run_gate(payload: dict) -> dict[str, object]:
            env = os.environ.copy()
            env["PLAIN_ENGLISH_STATE_DIR"] = str(self.state_dir)
            res = subprocess.run(
                ["/usr/bin/env", "bash", str(gate_sh)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env=env,
            )
            return {"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}

        # 1. Passing fixture
        pass_fix = fixtures["passing"]
        pass_res = run_gate(pass_fix["payload"])
        self.assertEqual(pass_res["returncode"], pass_fix["result"]["returncode"])
        self.assertEqual(pass_res["stderr"], pass_fix["result"]["stderr"])

        # 2. Blocking fixture
        block_fix = fixtures["blocking"]
        block_res = run_gate(block_fix["payload"])
        self.assertEqual(block_res["returncode"], block_fix["result"]["returncode"])
        self.assertEqual(block_res["stderr"], block_fix["result"]["stderr"])

        # 3. Escape sequence fixture
        esc_fix = fixtures["escape_sequence"]
        for expected in esc_fix["attempts"]:
            esc_res = run_gate(esc_fix["payload"])
            self.assertEqual(esc_res["returncode"], expected["returncode"])
            self.assertEqual(esc_res["stderr"], expected["stderr"])

    def test_origin_classification(self) -> None:
        # Write marks all findings new
        doc_write = "Delve into the details. Also utilize the tools."
        write_payload = {
            "session_id": "test-origin-write",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/test.md", "content": doc_write},
        }
        linter.run_hook(json.dumps(write_payload))
        events = linter.read_events(self.state_dir)
        self.assertEqual(len(events), 1)
        findings = events[0]["findings"]
        self.assertTrue(len(findings) >= 2)
        for f in findings:
            self.assertEqual(f["origin"], "new")

        # Edit single replacement
        existing_doc = "Line 1 is clean.\nLine 2 is clean.\nLine 3 is clean.\n"
        test_file = self.state_dir / "edit_test.md"
        test_file.write_text(existing_doc, encoding="utf-8")

        edit_payload = {
            "session_id": "test-origin-edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(test_file),
                "old_string": "Line 2 is clean.",
                "new_string": "Delve into Line 2.",
                "replace_all": False,
            },
        }
        linter.run_hook(json.dumps(edit_payload))
        events = linter.read_events(self.state_dir)
        edit_event = [e for e in events if e.get("session_id") == "test-origin-edit"][0]
        self.assertEqual(edit_event["findings"][0]["origin"], "new")
        self.assertEqual(edit_event["findings"][0]["line"], 2)

        # Pure deletion
        test_file_del = self.state_dir / "del_test.md"
        test_file_del.write_text("Delve into line 1.\nDelve into line 2.\n", encoding="utf-8")
        del_payload = {
            "session_id": "test-origin-del",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(test_file_del),
                "old_string": "Delve into line 1.\n",
                "new_string": "",
                "replace_all": False,
            },
        }
        linter.run_hook(json.dumps(del_payload))
        events = linter.read_events(self.state_dir)
        del_event = [e for e in events if e.get("session_id") == "test-origin-del"][0]
        for f in del_event["findings"]:
            self.assertEqual(f["origin"], "existing")

        # Edit replace_all with multiple occurrences and line shifts
        test_file_multi = self.state_dir / "multi_test.md"
        # Line 1: clean, Line 2: TARGET, Line 3: clean, Line 4: TARGET, Line 5: clean
        test_file_multi.write_text("Line 1.\nTARGET.\nLine 3.\nTARGET.\nLine 5.\n", encoding="utf-8")
        multi_payload = {
            "session_id": "test-origin-multi",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(test_file_multi),
                "old_string": "TARGET.",
                "new_string": "Delve into multi A.\nDelve into multi B.",
                "replace_all": True,
            },
        }
        linter.run_hook(json.dumps(multi_payload))
        events = linter.read_events(self.state_dir)
        multi_event = [e for e in events if e.get("session_id") == "test-origin-multi"][0]
        # Occurrence 1 replaced line 2 with 2 lines -> lines 2, 3
        # Occurrence 2 was line 4 -> shifted to lines 5, 6
        for f in multi_event["findings"]:
            if f["rule"] == "banned-word":
                self.assertEqual(f["origin"], "new")
                self.assertIn(f["line"], [2, 3, 5, 6])
            else:
                self.assertEqual(f["origin"], "existing")

    def test_payload_bytes_and_words(self) -> None:
        # Write
        content = "One two three four."
        write_payload = {
            "session_id": "test-payload-write",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/test.md", "content": content},
        }
        linter.run_hook(json.dumps(write_payload))
        events = linter.read_events(self.state_dir)
        event = [e for e in events if e.get("session_id") == "test-payload-write"][0]
        self.assertEqual(event["payload_bytes"], len(content.encode("utf-8")))
        self.assertEqual(event["payload_words"], 4)

        # Edit
        test_file = self.state_dir / "payload_edit.md"
        test_file.write_text("Old content here.", encoding="utf-8")
        edit_payload = {
            "session_id": "test-payload-edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(test_file),
                "old_string": "Old content",
                "new_string": "New content replacing old",
                "replace_all": False,
            },
        }
        linter.run_hook(json.dumps(edit_payload))
        events = linter.read_events(self.state_dir)
        event = [e for e in events if e.get("session_id") == "test-payload-edit"][0]
        self.assertEqual(event["payload_bytes"], len("Old content".encode("utf-8")) + len("New content replacing old".encode("utf-8")))
        self.assertEqual(event["payload_words"], 2 + 4)

        # CLI
        doc_file = self.state_dir / "cli_doc.md"
        cli_content = "Word one two three."
        doc_file.write_text(cli_content, encoding="utf-8")
        subprocess.run([sys.executable, str(BIN_PATH), str(doc_file)], capture_output=True, env=os.environ)
        events = linter.read_events(self.state_dir)
        cli_event = [e for e in events if e.get("surface") == "cli"][0]
        self.assertEqual(cli_event["payload_bytes"], len(cli_content.encode("utf-8")))
        self.assertEqual(cli_event["payload_words"], 4)

    def test_capped_findings_and_findings_total(self) -> None:
        # Create a document with 25 violations
        lines = [f"Delve into item {i}." for i in range(25)]
        content = "\n".join(lines)
        payload = {
            "session_id": "test-cap",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/many_findings.md", "content": content},
        }
        linter.run_hook(json.dumps(payload))
        events = linter.read_events(self.state_dir)
        event = [e for e in events if e.get("session_id") == "test-cap"][0]
        self.assertGreaterEqual(event["findings_total"], 25)
        self.assertEqual(len(event["findings"]), 20)

    def test_duration_ms_timing(self) -> None:
        payload = {
            "session_id": "test-timing",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/timing.md", "content": "Simple text."},
        }
        linter.run_hook(json.dumps(payload))
        events = linter.read_events(self.state_dir)
        event = [e for e in events if e.get("session_id") == "test-timing"][0]
        self.assertIn("duration_ms", event)
        self.assertIsInstance(event["duration_ms"], (int, float))
        self.assertGreaterEqual(event["duration_ms"], 0.0)

    def test_lint_purity_no_events_emitted(self) -> None:
        events_before = linter.read_events(self.state_dir)
        # Calling lint directly must not emit events
        findings = linter.lint("Delve into the details.")
        self.assertTrue(len(findings) > 0)
        events_after = linter.read_events(self.state_dir)
        self.assertEqual(len(events_before), len(events_after))

    def test_turn_tick_reminder_hook_and_cli(self) -> None:
        reminder_sh = HOOK_DIR / "reminder.sh"
        env = os.environ.copy()
        env["PLAIN_ENGLISH_STATE_DIR"] = str(self.state_dir)

        # Run reminder hook
        res = subprocess.run(
            ["/usr/bin/env", "bash", str(reminder_sh)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Answer in one bold line first.", res.stdout)
        self.assertEqual(res.stderr, "")

        events = linter.read_events(self.state_dir)
        turn_events = [e for e in events if e.get("event") == "turn"]
        self.assertTrue(len(turn_events) >= 1)

        # Test linter.py --turn with session_id
        payload = {"session_id": "custom-session-123"}
        res2 = subprocess.run(
            [sys.executable, str(LIB_DIR / "linter.py"), "--turn"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res2.returncode, 0)
        events = linter.read_events(self.state_dir)
        custom_turn = [e for e in events if e.get("session_id") == "custom-session-123"]
        self.assertEqual(len(custom_turn), 1)

    def test_writer_resilience(self) -> None:
        # Point state dir to an unwriteable directory
        unwriteable = self.state_dir / "unwriteable"
        unwriteable.mkdir(mode=0o444)
        env = os.environ.copy()
        env["PLAIN_ENGLISH_STATE_DIR"] = str(unwriteable / "nested")

        # run_hook must not raise
        payload = {
            "session_id": "test-resilience",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/resilience.md", "content": "Simple text."},
        }
        res = linter.run_hook(json.dumps(payload))
        self.assertEqual(res, 0)

    def test_flagged_text_opt_out(self) -> None:
        os.environ["PLAIN_ENGLISH_LOG_FLAGGED_TEXT"] = "0"
        try:
            payload = {
                "session_id": "test-opt-out",
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/opt_out.md", "content": "Delve into this."},
            }
            linter.run_hook(json.dumps(payload))
            events = linter.read_events(self.state_dir)
            event = [e for e in events if e.get("session_id") == "test-opt-out"][0]
            for f in event["findings"]:
                self.assertNotIn("flagged_text", f)
        finally:
            os.environ.pop("PLAIN_ENGLISH_LOG_FLAGGED_TEXT", None)

    def test_log_rotation_8mb(self) -> None:
        log_path = self.state_dir / "events.jsonl"
        rot1 = self.state_dir / "events.1.jsonl"
        rot2 = self.state_dir / "events.2.jsonl"

        # Create dummy log at 8MB
        log_path.write_text("x" * (8 * 1024 * 1024 + 10) + "\n", encoding="utf-8")
        linter.record_turn_event("rot-test-1")
        self.assertTrue(rot1.is_file())
        self.assertTrue(log_path.is_file())

        # Fill log again and rotate to rot2
        log_path.write_text("y" * (8 * 1024 * 1024 + 10) + "\n", encoding="utf-8")
        linter.record_turn_event("rot-test-2")
        self.assertTrue(rot2.is_file())
        self.assertTrue(rot1.is_file())

    def test_summariser_rework_arithmetic_and_three_way_rollup(self) -> None:
        events = [
            # Session 1: file A blocked on attempt 1, blocked on attempt 2, passed on attempt 3
            {
                "ts": 1700000000.0,
                "event": "lint",
                "surface": "gate",
                "tool": "Write",
                "path": "/path/fileA.md",
                "decision": "block",
                "streak": 1,
                "payload_words": 100,
                "findings": [{"rule": "banned-word", "severity": "error", "origin": "new"}],
                "session_id": "s1",
            },
            {
                "ts": 1700000010.0,
                "event": "lint",
                "surface": "gate",
                "tool": "Write",
                "path": "/path/fileA.md",
                "decision": "block",
                "streak": 2,
                "payload_words": 100,
                "findings": [{"rule": "banned-word", "severity": "error", "origin": "new"}],
                "session_id": "s1",
            },
            {
                "ts": 1700000020.0,
                "event": "lint",
                "surface": "gate",
                "tool": "Write",
                "path": "/path/fileA.md",
                "decision": "pass",
                "streak": 3,
                "payload_words": 90,
                "findings": [],
                "session_id": "s1",
            },
            # Session 2: file B existing only block
            {
                "ts": 1700000100.0,
                "event": "lint",
                "surface": "gate",
                "tool": "Edit",
                "path": "/path/fileB.md",
                "decision": "block",
                "streak": 1,
                "payload_words": 20,
                "findings": [{"rule": "sentence-length", "severity": "error", "origin": "existing"}],
                "session_id": "s2",
            },
            # Session 3: file C mixed block
            {
                "ts": 1700000200.0,
                "event": "lint",
                "surface": "gate",
                "tool": "Edit",
                "path": "/path/fileC.md",
                "decision": "block",
                "streak": 1,
                "payload_words": 50,
                "findings": [
                    {"rule": "banned-word", "severity": "error", "origin": "new"},
                    {"rule": "sentence-length", "severity": "error", "origin": "existing"},
                ],
                "session_id": "s3",
            },
        ]
        summary = linter.summarize_events(events)
        self.assertEqual(summary["work"]["total_writes"], 5)
        self.assertEqual(summary["blocks_by_origin"]["new_only"], 2)
        self.assertEqual(summary["blocks_by_origin"]["existing_only"], 1)
        self.assertEqual(summary["blocks_by_origin"]["mixed"], 1)
        self.assertEqual(summary["blocks_by_origin"]["unresolvable_count"], 2)

        # Rework: attempts with streak > 0 are attempt 2 (100 words), attempt 3 (90 words)
        self.assertEqual(summary["cost"]["rework_rewrites"], 2)
        self.assertEqual(summary["cost"]["rework_words"], 190)

    def test_prevention_metric_real_files_and_fallback(self) -> None:
        summary = linter.get_prevention_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["rate"], 8.09)
        self.assertEqual(summary["date"], "2026-08-17")
        self.assertEqual(summary["source"], "eval/results/baseline-results.md")

        # Empty directory fallback
        empty_dir = self.state_dir / "empty_results"
        empty_dir.mkdir()
        fallback = linter.get_prevention_summary(results_dir=empty_dir)
        self.assertIsNone(fallback)

    def test_summariser_edge_cases_and_filtering(self) -> None:
        # Empty events
        summary = linter.summarize_events([])
        self.assertEqual(summary["total_events"], 0)
        self.assertEqual(summary["work"]["total_writes"], 0)

        # Filter --since
        now = 1700000000.0
        events = [
            {"ts": now - 10 * 86400, "event": "turn"},
            {"ts": now - 2 * 86400, "event": "turn"},
            {"ts": now, "event": "turn"},
        ]
        log_file = self.state_dir / "events.jsonl"
        log_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        read_7d = linter.read_events(self.state_dir, since="7d", now=now)
        self.assertEqual(len(read_7d), 2)

        read_1d = linter.read_events(self.state_dir, since="1d", now=now)
        self.assertEqual(len(read_1d), 1)

    def test_stats_and_report_cli(self) -> None:
        # Write some events
        linter.record_turn_event("turn-1")
        doc = self.state_dir / "stats_test.md"
        doc.write_text("Delve into this.\n", encoding="utf-8")
        subprocess.run([sys.executable, str(BIN_PATH), str(doc)], capture_output=True, env=os.environ)

        # Test stats
        res_stats = subprocess.run([sys.executable, str(BIN_PATH), "stats"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(res_stats.returncode, 0)
        self.assertIn("Plain English", res_stats.stdout)
        self.assertIn("Work", res_stats.stdout)

        # Test stats --json
        res_json = subprocess.run([sys.executable, str(BIN_PATH), "stats", "--json"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertIn("work", data)

        # Test report
        out_md = self.state_dir / "custom_report.md"
        res_report = subprocess.run([sys.executable, str(BIN_PATH), "report", "--out", str(out_md)], capture_output=True, text=True, env=os.environ)
        self.assertEqual(res_report.returncode, 0)
        self.assertTrue(out_md.is_file())
        self.assertIn("# Plain English telemetry", out_md.read_text(encoding="utf-8"))

    def test_unconfigured_test_context_skips_writing(self) -> None:
        # When PLAIN_ENGLISH_STATE_DIR is not set in os.environ and running under unittest,
        # _record_event must skip writing and never touch ~/.claude/plain-english/events.jsonl
        saved_env = os.environ.pop("PLAIN_ENGLISH_STATE_DIR", None)
        try:
            default_events = Path.home() / ".claude" / "plain-english" / "events.jsonl"
            existed_before = default_events.is_file()
            mtime_before = default_events.stat().st_mtime if existed_before else 0.0

            linter.record_turn_event("guard-test-turn")

            if existed_before:
                self.assertEqual(default_events.stat().st_mtime, mtime_before)
            else:
                self.assertFalse(default_events.is_file())
        finally:
            if saved_env is not None:
                os.environ["PLAIN_ENGLISH_STATE_DIR"] = saved_env


if __name__ == "__main__":
    unittest.main()
