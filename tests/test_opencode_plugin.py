"""Test the OpenCode gate plugin module.

Two failure modes are specific to this adapter and both are pinned here:

- OpenCode rejects a module outright if any export is not a function, and a
  module that loads but never fires looks identical to a working gate that
  finds nothing. The export-shape assertion and the invocation counter tell
  those apart.
- OpenCode evaluates tool.execute.before twice per tool call and only the
  second pass carries a callID. The no-callID pass must reach neither the
  linter nor the retry state.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hooks" / "opencode" / "copydesk-gate.js"

RUNNER = """
import { pathToFileURL } from "node:url";

const mod = await import(pathToFileURL(process.argv[2]).href);
for (const [name, value] of Object.entries(mod)) {
  if (typeof value !== "function") {
    console.error("export is not a function:", name);
    process.exit(1);
  }
}

const hooks = await mod.CopydeskGate({});
const handler = hooks["tool.execute.before"];
if (typeof handler !== "function") {
  console.error("no tool.execute.before handler");
  process.exit(1);
}

const args = { filePath: "/tmp/proof.md", content: "hello world\\n" };

// The first evaluation pass carries no callID and must not reach the linter.
await handler({ tool: "write" }, { args });
if (fs.readFileSync(marker(), "utf8") !== "") {
  console.error("the no-callID pass invoked the linter");
  process.exit(1);
}

// An error-severity verdict becomes a thrown denial.
process.env.FAKE_EXIT = "2";
let blocked = null;
try {
  await handler({ tool: "write", callID: "call-block", sessionID: "ses-1" }, { args });
} catch (error) {
  blocked = String(error.message);
}
if (blocked === null || !blocked.includes("CopyDesk denied")) {
  console.error("a blocking verdict did not throw:", blocked);
  process.exit(1);
}

// The same callID never reaches the linter twice.
try {
  await handler({ tool: "write", callID: "call-block", sessionID: "ses-1" }, { args });
} catch { }
if (fs.readFileSync(marker(), "utf8") !== "x") {
  console.error("verdict cache missed:", fs.readFileSync(marker(), "utf8"));
  process.exit(1);
}

// A pass verdict resolves silently.
process.env.FAKE_EXIT = "0";
await handler({ tool: "write", callID: "call-pass", sessionID: "ses-1" }, { args });

// An edit reaches the linter and is denied. OpenCode 1.18.21 declares the
// edit parameters as filePath, oldString, newString and an optional
// replaceAll, so those are the names translated here.
process.env.FAKE_EXIT = "2";
let editDenied = false;
try {
  await handler(
    { tool: "edit", callID: "call-edit", sessionID: "ses-1" },
    { args: { filePath: "/tmp/proof.md", oldString: "hello", newString: "goodbye" } },
  );
} catch {
  editDenied = true;
}
if (!editDenied) {
  console.error("an edit carrying a finding was not denied");
  process.exit(1);
}
if (fs.readFileSync(marker(), "utf8") !== "xxx") {
  console.error("the edit tool never reached the linter");
  process.exit(1);
}

// An edit missing oldString cannot be translated and must not be judged.
process.env.FAKE_EXIT = "2";
await handler(
  { tool: "edit", callID: "call-edit-partial", sessionID: "ses-1" },
  { args: { filePath: "/tmp/proof.md", newString: "goodbye" } },
);
if (fs.readFileSync(marker(), "utf8") !== "xxx") {
  console.error("an untranslatable edit reached the linter");
  process.exit(1);
}

// A non-Markdown target is none of the gate's business even when the fake
// linter would block.
process.env.FAKE_EXIT = "2";
await handler(
  { tool: "bash", callID: "call-other", sessionID: "ses-1" },
  { args: { command: "ls" } },
);

// The verdict cache is bounded. Fill it past its limit with distinct calls,
// then re-run the first callID: its entry is gone, so the linter runs again.
process.env.FAKE_EXIT = "0";
for (let i = 0; i < 80; i++) {
  await handler({ tool: "write", callID: `fill-${i}`, sessionID: "ses-1" }, { args });
}
const beforeEviction = fs.readFileSync(marker(), "utf8").length;
await handler({ tool: "write", callID: "fill-0", sessionID: "ses-1" }, { args });
if (fs.readFileSync(marker(), "utf8").length !== beforeEviction + 1) {
  console.error("the oldest verdict was still cached after 80 calls");
  process.exit(1);
}

console.log("PLUGIN-OK");
"""

HEADER = 'import * as fs from "node:fs";\n'
MARKER_FN = "const marker = () => process.env.MARKER;\n"
RUNNER_SOURCE = HEADER + MARKER_FN + RUNNER


class OpenCodePluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.marker = self.dir / "marker"
        self.marker.write_text("", encoding="utf-8")
        self.fake_linter = self.dir / "fake-linter.py"
        # Reads the payload, records the invocation, then exits with a code
        # the test chooses through FAKE_EXIT.
        self.fake_linter.write_text(
            "import sys, os\n"
            "sys.stdin.read()\n"
            "open(os.environ['MARKER'], 'a').write('x')\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n",
            encoding="utf-8",
        )
        self.runner = self.dir / "runner.mjs"
        self.runner.write_text(RUNNER_SOURCE, encoding="utf-8")

    def _runtime(self) -> str | None:
        for name in ("bun", "node"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def test_the_plugin_loads_fires_and_blocks(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            self.skipTest("neither bun nor node is available")
        env = dict(os.environ)
        env.update({
            "COPYDESK_LINTER": str(self.fake_linter),
            "MARKER": str(self.marker),
            "FAKE_EXIT": "0",
        })
        result = subprocess.run(
            [runtime, str(self.runner), str(PLUGIN)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PLUGIN-OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
