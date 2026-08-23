"""The consumer pre-commit gate: judge the lines a commit adds, not the file."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI = REPOSITORY_ROOT / "bin" / "copydesk"
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

# Over the 25-word warning line the rate rule counts, under the 40-word
# error line sentence-length blocks with: the only error it can produce is
# the document-scoped one under test.
LONG_WARN_BAND = (
    "One long testing sentence marches across thirty odd words here so that "
    "the rate rule counts it while the per sentence limit stays far below "
    "its forty word error line today."
)

def _short_sentences(count: int) -> str:
    return "\n".join(CLEAN for _ in range(count))


def _long_sentences(count: int) -> str:
    return "\n".join(LONG_WARN_BAND for _ in range(count))


class GitRepositoryTestCase(unittest.TestCase):
    """A throwaway repository with an isolated CopyDesk environment."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        os.symlink(CLI, self.bin_dir / "copydesk")
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update({
            "COPYDESK_STATE_DIR": str(self.state),
            "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            "XDG_STATE_HOME": str(self.root / "xdg-state"),
            "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
        })
        # This suite runs inside the repository's own pre-commit hook, where
        # git exports GIT_INDEX_FILE and friends for the outer commit. Any
        # one of them would point the throwaway repositories at the real
        # index; every one goes.
        for name in list(env):
            if name.startswith("GIT_"):
                del env[name]
        return env

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, env=self._env(),
        )
        if check and result.returncode != 0:
            raise AssertionError(f"git {args} failed: {result.stderr}")
        return result

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.bin_dir / "copydesk"), *args],
            capture_output=True, text=True, cwd=str(self.repo), env=self._env(),
        )

    def write(self, name: str, content: str) -> Path:
        path = self.repo / name
        path.write_text(content, encoding="utf-8")
        return path

    def commit_initial(self, name: str, content: str, *flags: str) -> None:
        self.write(name, content)
        self._git("add", name)
        self._git("commit", "-qm", "init", *flags)

    def staged(self, *names: str) -> None:
        self._git("add", *names)


class InstallCommandTests(GitRepositoryTestCase):
    def test_install_writes_an_executable_hook(self) -> None:
        result = self.cli("install", "--git-hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        hook_file = self.repo / ".git" / "hooks" / "pre-commit"
        self.assertTrue(hook_file.is_file())
        self.assertTrue(os.stat(hook_file).st_mode & stat.S_IXUSR)
        content = hook_file.read_text(encoding="utf-8")
        self.assertIn("# CopyDesk pre-commit gate", content)
        self.assertIn("check --staged", content)

    def test_install_twice_reports_already_installed(self) -> None:
        self.cli("install", "--git-hook")
        again = self.cli("install", "--git-hook")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already installed", again.stdout)

    def test_install_refreshes_a_drifted_copydesk_hook(self) -> None:
        self.cli("install", "--git-hook")
        hook_file = self.repo / ".git" / "hooks" / "pre-commit"
        hook_file.write_text(
            "# CopyDesk pre-commit gate\nstale\n", encoding="utf-8"
        )
        result = self.cli("install", "--git-hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("updated", result.stdout)

    def test_a_foreign_hook_is_never_overwritten(self) -> None:
        hook_file = self.repo / ".git" / "hooks" / "pre-commit"
        hook_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        result = self.cli("install", "--git-hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
        self.assertIn("skipped", result.stdout)
        self.assertIn("check --staged", result.stdout)

    def test_outside_a_repository_is_an_error(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        result = subprocess.run(
            [str(self.bin_dir / "copydesk"), "install", "--git-hook"],
            capture_output=True, text=True, cwd=str(outside), env=self._env(),
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("not a git repository", result.stderr)

    def test_install_without_a_choice_prints_usage(self) -> None:
        result = self.cli("install")
        self.assertEqual(result.returncode, 64)
        self.assertIn("usage:", result.stderr)


class StagedScopeTests(GitRepositoryTestCase):
    """Each acceptance case drives check --staged against a real index."""

    def test_a_clean_edit_passes_in_a_file_full_of_pre_existing_errors(self) -> None:
        self.commit_initial("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n")
        self.write("a.md", LONG_ERROR + "\n\nA short sentence has enough words now.\n")
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("refused", result.stdout)

    def test_an_added_banned_word_refuses_the_commit(self) -> None:
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("a.md", CLEAN + "\n\n" + BANNED + "\n")
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("banned-word", result.stdout)

    def test_a_pure_deletion_passes(self) -> None:
        self.commit_initial("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n\nAnother short sentence sits right here.\n")
        self.write("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n")
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_working_tree_is_never_judged(self) -> None:
        self.commit_initial("a.md", CLEAN + "\n")
        # Staged: clean. Working tree: a banned word nobody is committing.
        self.write("a.md", CLEAN + "\n\nAnother short sentence sits right here.\n")
        self.staged("a.md")
        self.write("a.md", CLEAN + "\n\n" + BANNED + "\n")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_new_staged_file_is_judged_whole(self) -> None:
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("new.md", BANNED + "\n")
        self.staged("new.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("banned-word", result.stdout)

    def test_non_markdown_stages_change_nothing(self) -> None:
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("notes.txt", BANNED + "\n")
        self.staged("notes.txt")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_warn_severity_reports_and_passes(self) -> None:
        # Between 25 and 40 words: sentence-length warns, never blocks.
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("a.md", CLEAN + "\n\n" + LONG_WARN_BAND + "\n")
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sentence-length", result.stdout)


class DocumentScopedRuleTests(GitRepositoryTestCase):
    """Whole-document rules have no hunk; they block when they newly fire."""

    def base_not_firing(self) -> str:
        return _short_sentences(30)

    def test_rate_blocks_only_when_it_newly_fires(self) -> None:
        # HEAD: 30 short sentences, rate silent. Staged: four 30-word
        # sentences arrive, pushing 4/34 past the 10% rate, which is an
        # error no other rule produces.
        self.commit_initial("a.md", self.base_not_firing())
        self.write("a.md", self.base_not_firing() + "\n\n" + _long_sentences(4))
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("long-sentence-rate", result.stdout)

    def test_rate_already_firing_in_head_does_not_block(self) -> None:
        # HEAD: 3 of 29 long, already past the rate. Staged appends two
        # more warn-band sentences: still firing, so not newly fired, and
        # nothing else in the change carries an error.
        base = _short_sentences(26) + "\n" + _long_sentences(3)
        self.commit_initial("a.md", base)
        self.write("a.md", base + "\n\n" + _long_sentences(2))
        self.staged("a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("refused", result.stdout)


class HunkFallbackTests(GitRepositoryTestCase):
    """Past the comparison cap, git's unified-0 hunks are the edit's footprint."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(linter, "OUTER_DIFF_LINE_CAP", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_added_banned_word_still_blocks_through_hunks(self) -> None:
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("a.md", CLEAN + "\n\n" + BANNED + "\n")
        self._git("add", "a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("banned-word", result.stdout)

    def test_a_deletion_join_is_charged_through_hunks(self) -> None:
        left = "the first half of the merged sentence stands here without any terminal punctuation and keeps going through twenty one words overall okay"
        middle = CLEAN
        right = "and the second half arrives right after the join pushing everything past forty words altogether today indeed right now"
        # HEAD: the halves stay under 40 words even joined to the middle
        # line, so nothing blocks before the deletion. Staged: the middle
        # line goes, one 40-plus-word sentence exists, and only the join
        # can own it.
        self.assertLess(len(left.split()) + len(middle.split()), 40)
        self.assertGreater(len(left.split()) + len(right.split()), 40)
        self.commit_initial("a.md", f"{left}\n{middle}\n{right}\n")
        self.write("a.md", f"{left}\n{right}\n")
        self._git("add", "a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sentence-length", result.stdout)

    def test_untouched_text_still_passes_through_hunks(self) -> None:
        self.commit_initial("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n")
        self.write("a.md", LONG_ERROR + "\n\nA short sentence has enough words now.\n")
        self._git("add", "a.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CommitIntegrationTests(GitRepositoryTestCase):
    """The hook as a user lives with it: real git commit, real exit codes."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.cli("install", "--git-hook").returncode, 0)

    def _commit(self, *flags: str) -> subprocess.CompletedProcess:
        return self._git("commit", "-qm", "subject", *flags, check=False)

    def test_a_clean_commit_goes_through(self) -> None:
        self.write("a.md", CLEAN + "\n")
        self._git("add", "a.md")
        result = self._commit()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_banned_word_refuses_and_no_verify_bypasses(self) -> None:
        self.write("a.md", CLEAN + "\n\n" + BANNED + "\n")
        self._git("add", "a.md")
        refused = self._commit()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("banned-word", refused.stdout + refused.stderr)
        bypassed = self._commit("--no-verify")
        self.assertEqual(bypassed.returncode, 0, bypassed.stderr)

    def test_pre_existing_errors_do_not_block_someone_elses_edit(self) -> None:
        # The imperfect baseline enters history through --no-verify: that is
        # the escape hatch working as documented. The next, clean edit then
        # commits without one.
        self.commit_initial("a.md", CLEAN + "\n")
        self.write("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n")
        self._git("add", "a.md")
        self._git("commit", "-qm", "seed", "--no-verify")
        self.write("a.md", LONG_ERROR + "\n\nA short sentence has enough words now.\n")
        self._git("add", "a.md")
        result = self._commit()
        self.assertEqual(result.returncode, 0, result.stderr)


class RenamedFileTests(GitRepositoryTestCase):
    def test_a_renamed_imperfect_file_is_judged_against_its_source(self) -> None:
        self.commit_initial("a.md", LONG_ERROR + "\n\n" + CLEAN + "\n")
        self._git("mv", "a.md", "b.md")
        self.write("b.md", LONG_ERROR + "\n\nA short sentence has enough words now.\n")
        self._git("add", "b.md")
        result = self.cli("check", "--staged")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HunkRangeUnitTests(unittest.TestCase):
    def test_ranges_cover_the_added_lines(self) -> None:
        masked = "alpha\nbeta\ngamma\n"
        diff = "@@ -0,0 +1,2 @@\n+alpha\n+beta\n"
        self.assertEqual(linter._added_char_ranges(diff, masked), [(0, 10)])

    def test_a_deletion_hunk_records_zero_width_point_at_the_join(self) -> None:
        masked = "one\ntwo\nthree\n"
        diff = "@@ -2 +1,0 @@\n"
        self.assertEqual(linter._added_char_ranges(diff, masked), [(4, 4)])

    def test_document_scoped_rules_set_holds_the_four_named_rules(self) -> None:
        self.assertEqual(
            linter.DOCUMENT_SCOPED_BLOCKING_RULES,
            {"long-sentence-rate", "avg-sentence-length", "sentence-variation", "list-dominated"},
        )


class DocumentationTests(unittest.TestCase):
    """--no-verify must sit where a reader meets it before they need it."""

    def test_readme_documents_the_command_and_the_escape_hatch(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("install --git-hook", readme)
        self.assertIn("--no-verify", readme)

    def test_the_changelog_records_the_surface_addition(self) -> None:
        changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]")[1].split("## [")[0]
        self.assertIn("install --git-hook", unreleased)
        self.assertIn("--staged", unreleased)

    def test_the_installed_hook_carries_the_escape_hatch_itself(self) -> None:
        import install as install_module

        self.assertIn("--no-verify", install_module.HOOK_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
