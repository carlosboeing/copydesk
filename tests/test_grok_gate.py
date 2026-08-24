"""Test the Grok Build TUI PreToolUse adapter.

The payloads mirror real captures from grok 1.0.5: a native new-file write
arrives as toolName "write" and an edit as "search_replace", both with
snake_case fields inside toolInput.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("grok_gate", ROOT / "hooks" / "grok-gate.py")
grok_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grok_gate)


def _write_payload(session: str = "11111111-2222-4333-8444-555555555555") -> dict:
    return {
        "hookEventName": "pre_tool_use",
        "sessionId": session,
        "permissionMode": "bypassPermissions",
        "toolName": "write",
        "toolInput": {"file_path": "/tmp/note.md", "content": "hello world\n"},
    }


class TranslateTests(unittest.TestCase):
    def test_a_native_write_maps_to_the_claude_name(self) -> None:
        translated = grok_gate.translate(_write_payload())
        self.assertEqual(translated["tool_name"], "Write")
        self.assertEqual(translated["tool_input"]["file_path"], "/tmp/note.md")

    def test_the_session_id_gains_the_grok_prefix(self) -> None:
        translated = grok_gate.translate(_write_payload())
        self.assertEqual(
            translated["session_id"],
            "grok-11111111-2222-4333-8444-555555555555",
        )

    def test_search_replace_maps_to_edit_and_gains_replace_all(self) -> None:
        payload = {
            "hookEventName": "pre_tool_use",
            "sessionId": "abc",
            "toolName": "search_replace",
            "toolInput": {
                "file_path": "/tmp/note.md",
                "old_string": "hello",
                "new_string": "goodbye",
            },
        }
        translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_name"], "Edit")
        self.assertEqual(translated["tool_input"]["replace_all"], False)

    def test_an_existing_replace_all_passes_through(self) -> None:
        payload = {
            "hookEventName": "pre_tool_use",
            "sessionId": "abc",
            "toolName": "search_replace",
            "toolInput": {
                "file_path": "/tmp/note.md",
                "old_string": "hello",
                "new_string": "world",
                "replace_all": True,
            },
        }
        self.assertIs(grok_gate.translate(payload)["tool_input"]["replace_all"], True)

    def test_a_relative_path_is_anchored_to_the_workspace_root(self) -> None:
        """Grok's own tool declaration lets the model send "a relative path
        in the workspace", and its hook contract names no working directory
        for the runner. Without anchoring, linter.py resolves the path
        against whatever directory the runner happens to use."""
        payload = _write_payload()
        payload["workspaceRoot"] = "/ws"
        payload["cwd"] = "/elsewhere"
        payload["toolInput"] = {"file_path": "docs/note.md", "content": "hello\n"}
        translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_input"]["file_path"], "/ws/docs/note.md")

    def test_cwd_anchors_when_the_payload_carries_no_workspace_root(self) -> None:
        payload = _write_payload()
        payload["cwd"] = "/elsewhere"
        payload["toolInput"] = {"file_path": "note.md", "content": "hello\n"}
        translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_input"]["file_path"], "/elsewhere/note.md")

    def test_the_environment_anchors_when_the_payload_carries_neither(self) -> None:
        payload = _write_payload()
        payload["toolInput"] = {"file_path": "note.md", "content": "hello\n"}
        with mock.patch.dict(os.environ, {"GROK_WORKSPACE_ROOT": "/env-root"}):
            translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_input"]["file_path"], "/env-root/note.md")

    def test_an_absolute_path_is_left_alone(self) -> None:
        payload = _write_payload()
        payload["workspaceRoot"] = "/ws"
        translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_input"]["file_path"], "/tmp/note.md")

    def test_an_unanchorable_relative_path_passes_through(self) -> None:
        """No root to anchor it, so the path arrives as it was sent. That is
        the behaviour before this resolution existed."""
        payload = _write_payload()
        payload["toolInput"] = {"file_path": "note.md", "content": "hello\n"}
        with mock.patch.dict(os.environ, {}, clear=True):
            translated = grok_gate.translate(payload)
        self.assertEqual(translated["tool_input"]["file_path"], "note.md")

    def test_other_tools_translate_to_nothing(self) -> None:
        payload = {"sessionId": "abc", "toolName": "read_file", "toolInput": {}}
        self.assertIsNone(grok_gate.translate(payload))

    def test_a_missing_session_translates_to_nothing(self) -> None:
        payload = _write_payload(session="")
        self.assertIsNone(grok_gate.translate(payload))

    def test_a_non_document_payload_translates_to_nothing(self) -> None:
        self.assertIsNone(grok_gate.translate("not a mapping"))


class FailOpenTests(unittest.TestCase):
    def run_main(self, stdin_text: str) -> tuple[int, str]:
        captured = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with mock.patch("sys.stdout", captured):
                code = grok_gate.main()
        return code, captured.getvalue()

    def test_malformed_json_exits_zero_and_says_nothing(self) -> None:
        code, output = self.run_main("{not json")
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_a_missing_linter_exits_zero_without_a_decision(self) -> None:
        with mock.patch.object(grok_gate, "_linter_path", return_value=None):
            code, output = self.run_main(json.dumps(_write_payload()))
        self.assertEqual(code, 0)
        self.assertEqual(output, "")


class EndToEndTests(unittest.TestCase):
    """The wrapper run exactly as Grok's hook runner runs it."""

    def setUp(self) -> None:
        self.state_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.state_dir)

    def _run(self, payload: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["COPYDESK_LINTER"] = str(ROOT / "lib" / "linter.py")
        env["COPYDESK_STATE_DIR"] = str(self.state_dir)
        env.pop("GROK_WORKSPACE_ROOT", None)
        env.pop("CLAUDE_PROJECT_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "grok-gate.py")],
            input=json.dumps(payload), capture_output=True, text=True, env=env,
            cwd=str(cwd) if cwd is not None else None,
        )

    def test_a_violating_write_returns_a_deny_decision_on_exit_zero(self) -> None:
        payload = _write_payload()
        payload["toolInput"] = {
            "file_path": "/tmp/dirty.md",
            "content": (
                "This is definitely just a quick update in order to fix the "
                "issue at hand.\nAdditionally it actually utilises several "
                "banned words that should absolutely trigger a block decision "
                "from the gate this time around.\n"
            ),
        }
        result = self._run(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("orphan-pointer", decision["reason"])

    def test_a_relative_path_write_is_denied_from_another_directory(self) -> None:
        """The runner's own directory must not decide whether the gate sees
        the file. This runs the wrapper from a directory that is not the
        workspace root, exactly as Grok gives no guarantee it will not."""
        workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workspace)
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere)
        # An edit, not a write: the linter reads the existing file from disk
        # to build the proposed document, so an unanchored relative path is
        # the case that fails. A write carries its content in the payload and
        # would block either way, proving nothing.
        (workspace / "dirty.md").write_text("One short line.\n", encoding="utf-8")
        payload = _write_payload()
        payload["toolName"] = "search_replace"
        payload["workspaceRoot"] = str(workspace)
        payload["toolInput"] = {
            "file_path": "dirty.md",
            "old_string": "One short line.",
            "new_string": (
                "This is definitely just a quick update in order to fix the "
                "issue at hand. Additionally it actually utilises several "
                "banned words that should absolutely trigger a block decision."
            ),
        }
        result = self._run(payload, cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), "the gate said nothing at all")
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "deny")

    def test_a_clean_write_is_allowed_in_silence(self) -> None:
        payload = _write_payload()
        payload["toolInput"] = {"file_path": "/tmp/clean.md", "content": "One short line.\n"}
        result = self._run(payload)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("decision", result.stdout)

    def test_retry_state_lands_under_the_grok_prefix(self) -> None:
        payload = _write_payload(session="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        payload["toolInput"] = {
            "file_path": "/tmp/blocked.md",
            "content": (
                "This is definitely just a quick update in order to fix the "
                "issue at hand.\n"
            ),
        }
        self._run(payload)
        names = [p.name for p in self.state_dir.glob("*.json")]
        self.assertEqual(names, ["grok-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.json"])


if __name__ == "__main__":
    unittest.main()
