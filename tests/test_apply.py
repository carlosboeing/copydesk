"""One real file, one write. Apply is all-or-nothing."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import apply  # noqa: E402


class SymlinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_two_symlinks_to_one_file_coalesce_into_one_write(self) -> None:
        real = self.home / "CLAUDE.md"
        real.write_text("original\n", encoding="utf-8")
        (self.home / "codex-AGENTS.md").symlink_to(real)
        (self.home / "agents-AGENTS.md").symlink_to(real)
        plan = apply.plan_targets(
            [self.home / "codex-AGENTS.md", self.home / "agents-AGENTS.md"], block="BLOCK"
        )
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].real, real.resolve())
        self.assertEqual(sorted(p.name for p in plan[0].aliases), ["agents-AGENTS.md", "codex-AGENTS.md"])

    def test_the_write_goes_through_the_link_not_over_it(self) -> None:
        real = self.home / "CLAUDE.md"
        real.write_text("original\n", encoding="utf-8")
        link = self.home / "AGENTS.md"
        link.symlink_to(real)
        apply.write_marked_block(link, "BLOCK")
        self.assertTrue(link.is_symlink())
        self.assertIn("BLOCK", real.read_text(encoding="utf-8"))


class MarkedBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_a_re_run_replaces_the_region(self) -> None:
        target = self.home / "AGENTS.md"
        target.write_text("keep me\n", encoding="utf-8")
        apply.write_marked_block(target, "FIRST")
        apply.write_marked_block(target, "SECOND")
        text = target.read_text(encoding="utf-8")
        self.assertIn("keep me", text)
        self.assertIn("SECOND", text)
        self.assertNotIn("FIRST", text)
        self.assertEqual(text.count("<!-- copydesk:start -->"), 1)

    def test_removing_the_block_leaves_the_file_as_it_was(self) -> None:
        target = self.home / "AGENTS.md"
        original = "keep me\n"
        target.write_text(original, encoding="utf-8")
        apply.write_marked_block(target, "FIRST")
        apply.remove_marked_block(target)
        self.assertEqual(target.read_text(encoding="utf-8"), original)


class RollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_a_failure_mid_apply_restores_everything(self) -> None:
        first = self.home / "a.md"
        first.write_text("a\n", encoding="utf-8")
        plan = apply.Plan(writes=[
            apply.Write(first, "new a"),
            apply.Write(self.home / "nope" / "b.md", "new b", must_exist=True),
        ])
        result = apply.execute(plan)
        self.assertFalse(result.ok)
        self.assertEqual(first.read_text(encoding="utf-8"), "a\n")
        self.assertFalse((self.home / "nope").exists())

    def test_a_successful_apply_keeps_one_backup_per_real_file(self) -> None:
        target = self.home / "settings.json"
        target.write_text("{}\n", encoding="utf-8")
        result = apply.execute(apply.Plan(writes=[apply.Write(target, '{"hooks": {}}')]))
        self.assertTrue(result.ok)
        backups = list(self.home.glob("settings.json.copydesk-backup-*"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
