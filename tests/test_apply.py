"""One real file, one write. Apply is all-or-nothing."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import apply  # noqa: E402


def install_block(path: Path, block: str) -> None:
    """Put a marked region into `path` the way setup does.

    Setup reads, splices and writes through a plan. These tests only need the
    region to be there before they exercise removal, so they take the same
    route rather than hand-rolling the marker text.
    """
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    result = apply.execute(
        apply.Plan(writes=[apply.Write(path, apply.splice_marked_block(existing, block))])
    )
    assert result.ok, result.message


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
        # Setup names the link, `plan_targets` resolves it, and the write goes
        # to the resolved file. Writing the link itself would replace one
        # shared instruction file with several.
        real = self.home / "CLAUDE.md"
        real.write_text("original\n", encoding="utf-8")
        link = self.home / "AGENTS.md"
        link.symlink_to(real)
        [target] = apply.plan_targets([link], block="BLOCK")
        install_block(target.real, target.block)
        self.assertTrue(link.is_symlink())
        self.assertIn("BLOCK", real.read_text(encoding="utf-8"))
        self.assertIn("original", real.read_text(encoding="utf-8"))


class MarkedBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_a_re_run_replaces_the_region(self) -> None:
        first = apply.splice_marked_block("keep me\n", "FIRST")
        second = apply.splice_marked_block(first, "SECOND")
        self.assertIn("keep me", second)
        self.assertIn("SECOND", second)
        self.assertNotIn("FIRST", second)
        self.assertEqual(second.count(apply.MARKER_START), 1)

    def test_a_first_run_appends_one_blank_line_and_no_more(self) -> None:
        # The separator rule. `remove_marked_block` strips what this adds, so
        # the two have to agree on it for an uninstall to be byte-exact.
        self.assertEqual(
            apply.splice_marked_block("keep me\n", "BLOCK"),
            "keep me\n\n<!-- copydesk:start -->\nBLOCK\n<!-- copydesk:end -->\n",
        )
        self.assertEqual(
            apply.splice_marked_block("keep me\n\n", "BLOCK"),
            "keep me\n\n<!-- copydesk:start -->\nBLOCK\n<!-- copydesk:end -->\n",
        )
        self.assertEqual(
            apply.splice_marked_block("", "BLOCK"),
            "<!-- copydesk:start -->\nBLOCK\n<!-- copydesk:end -->\n",
        )

    def test_removing_the_block_leaves_the_file_as_it_was(self) -> None:
        target = self.home / "AGENTS.md"
        original = "keep me\n"
        target.write_text(original, encoding="utf-8")
        install_block(target, "FIRST")
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

    def test_a_write_failing_after_an_earlier_one_restores_the_earlier_one(self) -> None:
        # The test above returns at pre-validation, so nothing is written and
        # the rollback never runs. This one fails on the second write, after
        # the first has already changed the file, which is the only case the
        # in-memory restore exists for. The second path is under a file rather
        # than a directory, so the failure does not depend on permissions.
        first = self.home / "a.md"
        first.write_text("a\n", encoding="utf-8")
        result = apply.execute(apply.Plan(writes=[
            apply.Write(first, "new a"),
            apply.Write(first / "b.md", "new b"),
        ]))
        self.assertFalse(result.ok)
        self.assertEqual(first.read_text(encoding="utf-8"), "a\n")

    def test_a_successful_apply_leaves_no_snapshot_behind(self) -> None:
        # Setup reads settings.json, which carries credentials. A copy beside
        # it would be created at the process umask and never deleted.
        target = self.home / "settings.json"
        target.write_text('{"apiKey": "s3cret"}\n', encoding="utf-8")
        result = apply.execute(apply.Plan(writes=[apply.Write(target, '{"hooks": {}}')]))
        self.assertTrue(result.ok)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["settings.json"])
        self.assertNotIn("s3cret", target.read_text(encoding="utf-8"))

    def test_a_planned_removal_happens_after_the_writes_succeed(self) -> None:
        # The style migration removes the retired per-level files only once
        # the new single file is on disk, so a failed write never leaves an
        # install with neither.
        styles = self.home / "output-styles"
        styles.mkdir()
        legacy = styles / "copydesk-medium.md"
        legacy.write_text("old\n", encoding="utf-8")
        fresh = styles / "copydesk.md"
        result = apply.execute(apply.Plan(
            writes=[apply.Write(fresh, "new")],
            removes=[legacy],
        ))
        self.assertTrue(result.ok, result.message)
        self.assertTrue(fresh.is_file())
        self.assertFalse(legacy.exists())

    def test_an_absent_removal_target_is_not_a_failure(self) -> None:
        # A second repair finds no leftovers; the plan still names them so
        # the first run's review panel stays honest about intent.
        styles = self.home / "output-styles"
        styles.mkdir()
        result = apply.execute(apply.Plan(
            writes=[apply.Write(styles / "copydesk.md", "new")],
            removes=[styles / "copydesk-low.md"],
        ))
        self.assertTrue(result.ok, result.message)

    def test_a_failed_write_rolls_the_removal_back(self) -> None:
        # All-or-nothing covers deletions too: the retired files come back
        # if the plan fails, leaving the install exactly as it was found.
        legacy = self.home / "copydesk-low.md"
        legacy.write_text("old\n", encoding="utf-8")
        result = apply.execute(apply.Plan(
            writes=[
                apply.Write(self.home / "copydesk.md", "new"),
                apply.Write(self.home / "copydesk.md" / "child", "boom"),
            ],
            removes=[legacy],
        ))
        self.assertFalse(result.ok)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()


class UninstallResidueTests(unittest.TestCase):
    """Uninstall leaves no trace of a file CopyDesk created, and every trace
    of one it only edited."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_a_file_copydesk_created_is_removed_when_its_region_goes(self) -> None:
        target = self.home / "AGENTS.md"
        install_block(target, "BLOCK")
        self.assertTrue(target.is_file())
        apply.remove_marked_block(target)
        self.assertFalse(target.exists(), "an empty file CopyDesk created is litter")

    def test_a_file_that_had_content_survives(self) -> None:
        # The control. Without it the test above could pass by deleting
        # everything, which is the failure that actually matters.
        target = self.home / "AGENTS.md"
        original = "# My own instructions\n\nKeep these.\n"
        target.write_text(original, encoding="utf-8")
        install_block(target, "BLOCK")
        apply.remove_marked_block(target)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_a_file_of_only_whitespace_counts_as_empty(self) -> None:
        target = self.home / "AGENTS.md"
        target.write_text("\n\n", encoding="utf-8")
        install_block(target, "BLOCK")
        apply.remove_marked_block(target)
        self.assertFalse(target.exists())

    def test_settings_reduced_to_an_empty_object_is_removed(self) -> None:
        settings = self.home / "settings.json"
        settings.write_text(
            '{"hooks": {"PreToolUse": [{"matcher": "Write",'
            ' "hooks": [{"type": "command", "command": "~/.claude/hooks/copydesk/gate.sh"}]}]}}',
            encoding="utf-8",
        )
        apply._remove_copydesk_hooks(settings)
        self.assertFalse(settings.exists(), "a settings.json holding only {} carries no config")

    def test_settings_with_other_keys_survives(self) -> None:
        settings = self.home / "settings.json"
        settings.write_text(
            '{"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Write",'
            ' "hooks": [{"type": "command", "command": "~/.claude/hooks/copydesk/gate.sh"}]}]}}',
            encoding="utf-8",
        )
        apply._remove_copydesk_hooks(settings)
        self.assertTrue(settings.is_file())
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"model": "opus"})

    def test_settings_keeping_a_foreign_hook_survives(self) -> None:
        settings = self.home / "settings.json"
        settings.write_text(
            '{"hooks": {"PreToolUse": [{"matcher": "Bash",'
            ' "hooks": [{"type": "command", "command": "~/my-own-hook.sh"}]}]}}',
            encoding="utf-8",
        )
        apply._remove_copydesk_hooks(settings)
        self.assertTrue(settings.is_file())
        self.assertIn("my-own-hook.sh", settings.read_text(encoding="utf-8"))

    def test_empty_directories_are_pruned_up_to_the_harness_home(self) -> None:
        hooks = self.home / ".claude" / "hooks" / "copydesk"
        hooks.mkdir(parents=True)
        (hooks / "gate.sh").write_text("x", encoding="utf-8")
        styles = self.home / ".claude" / "output-styles"
        styles.mkdir(parents=True)
        (styles / "copydesk-low.md").write_text("x", encoding="utf-8")
        result = apply.remove_owned([
            apply.Target(real=hooks, kind="created"),
            apply.Target(real=styles / "copydesk-low.md", kind="created"),
        ], homes=[self.home / ".claude"])
        self.assertTrue(result.ok, result.message)
        self.assertFalse((self.home / ".claude" / "hooks").exists())
        self.assertFalse(styles.exists())
        self.assertTrue((self.home / ".claude").is_dir(), "the harness home is never removed")

    def test_a_directory_with_other_content_is_kept(self) -> None:
        # The control for pruning: only empty directories go.
        styles = self.home / ".claude" / "output-styles"
        styles.mkdir(parents=True)
        (styles / "copydesk-low.md").write_text("x", encoding="utf-8")
        (styles / "mine.md").write_text("keep", encoding="utf-8")
        apply.remove_owned([apply.Target(real=styles / "copydesk-low.md", kind="created")],
                           homes=[self.home / ".claude"])
        self.assertTrue(styles.is_dir())
        self.assertEqual([p.name for p in styles.iterdir()], ["mine.md"])
