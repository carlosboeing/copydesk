"""The setup plan and uninstall cover the Grok and OpenCode gates."""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "lib"))

import wizard  # noqa: E402


class HarnessGatePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def _plan(self, tools: list[str]) -> wizard.apply.Plan:
        return wizard._build_plan(
            home=self.home,
            config_path=self.home / "config" / "copydesk" / "config.json",
            config_body="{}",
            selected_tools=tools,
            resolved_config={},
            settings_doc={},
        )

    def _writes(self, plan: wizard.apply.Plan) -> dict[Path, str]:
        return {write.path: write.content for write in plan.writes}

    def test_the_grok_plan_bundles_the_gate_and_registers_it(self) -> None:
        writes = self._writes(self._plan(["grok"]))
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        linter = self.home / ".grok" / "hooks" / "copydesk" / "linter.py"
        preset = self.home / ".grok" / "hooks" / "copydesk" / "rules" / "plain.json"
        registration = self.home / ".grok" / "hooks" / "copydesk.json"
        for path in (gate, linter, preset, registration):
            self.assertIn(path, writes, path)
        registered = json.loads(writes[registration])
        group = registered["hooks"]["PreToolUse"][0]
        self.assertEqual(group["matcher"], "Write|Edit")
        handler = group["hooks"][0]
        self.assertEqual(handler["command"], str(gate))
        self.assertGreaterEqual(handler["timeout"], 10)

    def test_the_grok_gate_source_carries_the_executable_bit(self) -> None:
        # Setup chmods the installed copy after apply.execute, and git
        # preserves this mode on checkout, so a fresh clone installs a
        # runnable gate even before setup's own chmod runs.
        source = ROOT / "hooks" / "grok-gate.py"
        self.assertTrue(source.exists())
        self.assertTrue(os.access(source, os.X_OK))

    def test_setup_installs_a_working_grok_gate_end_to_end(self) -> None:
        (self.home / ".grok").mkdir()
        env = dict(os.environ)
        env.update({
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": str(self.home / "nothing"),
        })
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        registration = self.home / ".grok" / "hooks" / "copydesk.json"
        self.assertTrue(gate.is_file(), result.stdout)
        self.assertTrue(os.access(gate, os.X_OK))
        self.assertTrue(registration.is_file())
        registered = json.loads(registration.read_text(encoding="utf-8"))
        self.assertEqual(registered["hooks"]["PreToolUse"][0]["matcher"], "Write|Edit")
        self.assertTrue((self.home / ".grok" / "hooks" / "copydesk" / "rules" / "plain.json").is_file())

    def test_setup_makes_the_grok_gate_executable_with_claude_selected(self) -> None:
        (self.home / ".grok").mkdir()
        (self.home / ".claude").mkdir()
        env = dict(os.environ)
        env.update({
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": str(self.home / "nothing"),
        })
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        self.assertTrue(os.access(gate, os.X_OK), result.stdout)

    def test_the_opencode_plan_installs_one_plugin_and_a_bundle(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home / "config")}):
            writes = self._writes(self._plan(["opencode"]))
        plugin = self.home / "config" / "opencode" / "plugins" / "copydesk-gate.js"
        linter = self.home / "config" / "opencode" / "copydesk" / "linter.py"
        for path in (plugin, linter):
            self.assertIn(path, writes, path)
        plugin_names = [p.name for p in writes if p.parent.name == "plugins"]
        self.assertEqual(plugin_names, ["copydesk-gate.js"])

    def test_an_unrelated_tool_plans_neither_gate(self) -> None:
        writes = self._writes(self._plan(["cursor"]))
        for path in writes:
            self.assertNotIn(".grok", str(path))
            self.assertNotIn("copydesk-gate.js", path.name)


class HarnessGateUninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_uninstall_lists_the_grok_and_opencode_files(self) -> None:
        grok_dir = self.home / ".grok" / "hooks" / "copydesk"
        grok_dir.mkdir(parents=True)
        (grok_dir / "grok-gate.py").write_text("", encoding="utf-8")
        (self.home / ".grok" / "hooks" / "copydesk.json").write_text("{}", encoding="utf-8")
        oc_root = self.home / "config" / "opencode"
        (oc_root / "plugins").mkdir(parents=True)
        (oc_root / "plugins" / "copydesk-gate.js").write_text("", encoding="utf-8")
        (oc_root / "copydesk").mkdir()
        (oc_root / "copydesk" / "linter.py").write_text("", encoding="utf-8")

        out = io.StringIO()
        with mock.patch.dict(os.environ, {
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
        }):
            code = wizard.run_uninstall(["--dry-run"], stdin=io.StringIO(""), stdout=out)
        self.assertEqual(code, 0)
        listing = out.getvalue()
        for named in (
            grok_dir,
            self.home / ".grok" / "hooks" / "copydesk.json",
            oc_root / "plugins" / "copydesk-gate.js",
            oc_root / "copydesk",
        ):
            self.assertIn(str(named), listing)


if __name__ == "__main__":
    unittest.main()
