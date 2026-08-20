"""The commits gate runs where git writes the message."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wizard

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class CommitMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True, env=CLEAN_ENV)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True, env=CLEAN_ENV)
        subprocess.run(["git", "config", "user.name", "CopyDesk Test"], cwd=self.repo, check=True, env=CLEAN_ENV)
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "git-hooks" / "commit-msg", hooks / "commit-msg")
        os.chmod(hooks / "commit-msg", 0o755)
        (self.repo / "copydesk.config.json").write_text('{"version": 1}', encoding="utf-8")

    def test_the_control_commit_succeeds_without_the_hook(self) -> None:
        (self.repo / ".git" / "hooks" / "commit-msg").unlink()
        self.assertEqual(self._commit("Expire reset tokens after first use").returncode, 0)

    def _commit(self, message: str) -> subprocess.CompletedProcess:
        (self.repo / "f.txt").write_text(message, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, env=CLEAN_ENV)
        return subprocess.run(
            ["git", "commit", "-m", message], cwd=self.repo, capture_output=True, text=True,
            env=dict(CLEAN_ENV, COPYDESK_BIN=str(ROOT / "bin" / "copydesk")),
        )

    def _commit_with_stub(self, exit_code: int) -> subprocess.CompletedProcess:
        """Commit with a CopyDesk that always exits with `exit_code`."""
        stub = self.repo / "stub-copydesk"
        stub.write_text(f"#!/bin/sh\necho 'stub says {exit_code}' >&2\nexit {exit_code}\n",
                        encoding="utf-8")
        stub.chmod(0o755)
        self.addCleanup(stub.unlink)
        (self.repo / "f.txt").write_text(str(exit_code), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, env=CLEAN_ENV)
        return subprocess.run(
            ["git", "commit", "-m", "Expire reset tokens after first use"],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(CLEAN_ENV, COPYDESK_BIN=str(stub)),
        )

    def test_a_clean_message_commits(self) -> None:
        self.assertEqual(self._commit("Expire reset tokens after first use").returncode, 0)

    def test_an_announcing_opener_is_refused(self) -> None:
        result = self._commit("This commit expires reset tokens after first use")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("announcing-opener", result.stderr + result.stdout)

    def test_a_subject_over_72_characters_is_refused(self) -> None:
        long_subject = "Expire the password reset tokens after their very first successful use here"
        result = self._commit(long_subject)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("72", result.stderr + result.stdout)

    def test_no_verify_skips_the_hook(self) -> None:
        (self.repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, env=CLEAN_ENV)
        result = subprocess.run(
            ["git", "commit", "--no-verify", "-m", "This commit is robust"],
            cwd=self.repo, capture_output=True, text=True, env=CLEAN_ENV,
        )
        self.assertEqual(result.returncode, 0)

    def test_a_missing_linter_fails_open(self) -> None:
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "This commit is robust"],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(CLEAN_ENV, COPYDESK_BIN="/nonexistent/copydesk"),
        )
        self.assertEqual(result.returncode, 0)

    def test_a_broken_config_fails_open(self) -> None:
        (self.repo / "copydesk.config.json").write_text("{ not json", encoding="utf-8")
        result = self._commit("This commit is robust")
        self.assertEqual(result.returncode, 0, "a malformed config must not block a commit")

    def test_an_internal_error_fails_open(self) -> None:
        result = self._commit_with_stub(exit_code=70)
        self.assertEqual(result.returncode, 0)

    def test_a_usage_error_fails_open(self) -> None:
        self.assertEqual(self._commit_with_stub(exit_code=64).returncode, 0)

    def test_only_exit_one_blocks(self) -> None:
        self.assertNotEqual(self._commit_with_stub(exit_code=1).returncode, 0)

    def test_a_comment_line_is_not_linted(self) -> None:
        self.assertEqual(self._commit("Expire reset tokens\n\n# Please enter the commit message").returncode, 0)


class HookInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True, env=CLEAN_ENV)
        hooks = self.repo / ".git" / "hooks"
        if hooks.is_dir():
            for sample in hooks.glob("*.sample"):
                sample.unlink()

    def test_the_hooks_directory_comes_from_git(self) -> None:
        found = wizard.hooks_directory(self.repo)
        self.assertEqual(found, (self.repo / ".git" / "hooks").resolve())

    def test_core_hooks_path_is_honoured(self) -> None:
        custom = self.repo / "my-hooks"
        custom.mkdir()
        subprocess.run(["git", "config", "core.hooksPath", "my-hooks"], cwd=self.repo, check=True, env=CLEAN_ENV)
        self.assertEqual(wizard.hooks_directory(self.repo), custom.resolve())

    def test_a_linked_worktree_gets_its_own_hooks_path(self) -> None:
        (self.repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, env=CLEAN_ENV)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                       cwd=self.repo, check=True, env=CLEAN_ENV)
        tree = self.repo / ".worktrees" / "claude" / "feat"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "feat", str(tree)], cwd=self.repo, check=True, env=CLEAN_ENV)
        self.assertIsNotNone(wizard.hooks_directory(tree))

    def test_an_existing_foreign_hook_is_refused_not_moved(self) -> None:
        hook = self.repo / ".git" / "hooks" / "commit-msg"
        hook.write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
        result = wizard.install_commit_hook(self.repo)
        self.assertFalse(result.installed)
        self.assertIn("already exists", result.message)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\necho theirs\n")
        self.assertEqual(list((self.repo / ".git" / "hooks").glob("commit-msg.*")), [])

    def test_a_copydesk_hook_is_replaced(self) -> None:
        wizard.install_commit_hook(self.repo)
        self.assertTrue(wizard.install_commit_hook(self.repo).installed)

    def test_outside_a_repository_it_says_so(self) -> None:
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain)
        self.assertIsNone(wizard.hooks_directory(plain))
