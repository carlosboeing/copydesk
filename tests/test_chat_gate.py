"""The chat Stop hook: one reply per turn, judged like a document, keyed on session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import linter  # noqa: E402

CHAT_GATE = REPOSITORY_ROOT / "hooks" / "chat-gate.sh"


def _reply(text: str) -> dict[str, str]:
    return {"session_id": "chat-gate-test", "last_assistant_message": text}


class ChatGateTests(unittest.TestCase):
    def setUp(self) -> None:
        """Isolate discovery so the shipped preset judges, not the developer's."""
        self._config_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_home.cleanup)

    def opt_into_blocking(self) -> None:
        """Set `channels.chat.gate` to `block` in the isolated user config.

        Every refusal assertion below runs through this, because refusing is
        opt-in. A test that forgets the call reads exit 0 and fails.
        """
        directory = Path(self._config_home.name) / "copydesk"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(
            json.dumps({"version": 1, "channels": {"chat": {"gate": "block"}}}),
            encoding="utf-8",
        )

    def run_gate(self, payload: object, state_dir: str) -> subprocess.CompletedProcess:
        """Run the real shell wrapper with isolated retry-state storage."""
        environment = os.environ.copy()
        environment["COPYDESK_STATE_DIR"] = state_dir
        environment["XDG_CONFIG_HOME"] = self._config_home.name
        return subprocess.run(
            ["/usr/bin/env", "bash", str(CHAT_GATE)],
            input=json.dumps(payload) if not isinstance(payload, str) else payload,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def _lint_isolated(self, message: str) -> list:
        """lint() as the gate sees it, with no user config in the cascade."""
        saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._config_home.name
        try:
            return linter.lint(message, channel="chat")
        finally:
            if saved is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = saved

    def test_a_clean_reply_exits_zero(self) -> None:
        """Removing nothing here must keep this passing; it is the control."""
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(_reply("Read the report before approving it."), state_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_a_banned_word_refuses_the_stop_and_prints_the_finding(self) -> None:
        """The control for every scan that returns nothing in this file."""
        self.opt_into_blocking()
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(_reply("The draft carries the answer."), state_dir)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"(?m)^1:verb-jargon:")

    def test_the_default_reports_a_banned_word_and_shows_the_reader_nothing(self) -> None:
        """The reason for the default: Claude Code leaves a refused reply on
        screen and appends its replacement, so a refusal duplicates the answer.

        The control is the test above, which feeds the same reply through the
        same wrapper with `channels.chat.gate` set to `block` and reads exit 2.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(_reply("The draft carries the answer."), state_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_the_default_records_the_finding_it_did_not_refuse(self) -> None:
        """Silence to the reader is not silence to telemetry."""
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(_reply("The draft carries the answer."), state_dir)
            events = [
                e for e in linter.read_events(Path(state_dir))
                if e.get("surface") == "chat"
            ]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["decision"] for e in events], ["warn"])
        self.assertEqual(
            [(f["rule"], f["severity"]) for f in events[0]["findings"]],
            [("verb-jargon", "error")],
        )
        self.assertEqual(events[0]["blocking_rule_totals"], {"verb-jargon": 1})

    def test_the_default_writes_no_retry_state(self) -> None:
        """Nothing is refused, so nothing is counted towards a retry limit."""
        with tempfile.TemporaryDirectory() as state_dir:
            for _ in range(4):
                result = self.run_gate(_reply("The draft carries the answer."), state_dir)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
            sessions = Path(state_dir) / "sessions"
            written = sorted(p.name for p in sessions.glob("*.chat")) if sessions.is_dir() else []
        self.assertEqual(written, [])

    def test_stop_hook_active_passes_the_turn_through(self) -> None:
        """Claude Code says it is already continuing because of a Stop hook.

        The session state file is the only other bound on the loop and it
        expires, so refusing again is what leaves the loop unbounded.
        """
        self.opt_into_blocking()
        with tempfile.TemporaryDirectory() as state_dir:
            without = self.run_gate(_reply("The draft carries the answer."), state_dir)
            with_flag = self.run_gate(
                {**_reply("The draft carries the answer."), "stop_hook_active": True},
                state_dir,
            )
        self.assertEqual(without.returncode, 2, without.stderr)
        self.assertEqual(with_flag.returncode, 0, with_flag.stderr)

    def test_a_two_sentence_reply_is_not_judged_by_document_statistics(self) -> None:
        """Rate rules need a whole document; two sentences are not one."""
        message = (
            "One very long sentence rolls on past the comfortable mark and keeps "
            "going through clause after clause until it finally arrives. "
            "A second sentence follows it with more words than any short reply "
            "needs, which is the point of this fixture."
        )
        findings = self._lint_isolated(message)
        self.assertEqual(
            [f.check for f in findings if f.check in ("long-sentence-rate", "avg-sentence-length")],
            [],
        )
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(_reply(message), state_dir)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_malformed_payload_fails_open(self) -> None:
        payloads = ["not json at all", "", "[1, 2, 3]", {"session_id": 42}]
        with tempfile.TemporaryDirectory() as state_dir:
            for payload in payloads:
                with self.subTest(payload=payload):
                    result = self.run_gate(payload, state_dir)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_payload_with_no_reply_fails_open(self) -> None:
        payloads = [
            {"session_id": "chat-gate-test"},
            {"session_id": "chat-gate-test", "last_assistant_message": ""},
            {"session_id": "chat-gate-test", "last_assistant_message": 12},
        ]
        with tempfile.TemporaryDirectory() as state_dir:
            for payload in payloads:
                with self.subTest(payload=payload):
                    result = self.run_gate(payload, state_dir)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_third_unresolved_attempt_passes_through_and_records_the_escape(self) -> None:
        """Reusing RETRY_LIMIT means write-gate parity: two refusals, then escape."""
        self.opt_into_blocking()
        with tempfile.TemporaryDirectory() as state_dir:
            first = self.run_gate(_reply("The draft carries the answer."), state_dir)
            second = self.run_gate(_reply("The draft carries the answer again."), state_dir)
            third = self.run_gate(_reply("It still carries weight here."), state_dir)
            events = [
                e for e in linter.read_events(Path(state_dir))
                if e.get("surface") == "chat"
            ]
            # After an escape the state is spent: the next offending turn blocks anew.
            fourth = self.run_gate(_reply("And carries weight once more."), state_dir)

        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn("passed after", third.stderr)
        self.assertEqual([e["decision"] for e in events], ["block", "block", "escape"])
        self.assertEqual(fourth.returncode, 2, fourth.stderr)

    def test_a_second_sessions_state_does_not_affect_the_first(self) -> None:
        self.opt_into_blocking()
        with tempfile.TemporaryDirectory() as state_dir:
            first_block = self.run_gate(
                {**_reply("The draft carries the answer."), "session_id": "session-a"},
                state_dir,
            )
            other_clean = self.run_gate(
                {**_reply("Read the report before approving it."), "session_id": "session-b"},
                state_dir,
            )
            other_offence = self.run_gate(
                {**_reply("Another draft carries nothing."), "session_id": "session-b"},
                state_dir,
            )
            first_retry = self.run_gate(
                {**_reply("The same draft carries the answer."), "session_id": "session-a"},
                state_dir,
            )

        self.assertEqual(first_block.returncode, 2, first_block.stderr)
        self.assertEqual(other_clean.returncode, 0, other_clean.stderr)
        # Session B's own first offence is streak 1, not streak 3.
        self.assertEqual(other_offence.returncode, 2, other_offence.stderr)
        # Session A is still on its second attempt rather than already escaped.
        self.assertEqual(first_retry.returncode, 2, first_retry.stderr)


if __name__ == "__main__":
    unittest.main()
