"""`copydesk hook add|remove|list` manages the commit-msg hook across repositories.

Every test maps to a line of the design's acceptance list. The registry lives
in COPYDESK_STATE_DIR, so a suite run never touches the real one; repositories
are temporary and never the checkout the suite runs from.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import hook  # noqa: E402
import linter  # noqa: E402

# A suite run from inside a worktree inherits GIT_DIR and its siblings, which
# would point a temporary repository back at the real one.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

FOREIGN_HOOK = "#!/bin/sh\necho theirs\n"

# A hook whose author set errexit. The appended block runs under it.
STRICT_HOOK = "#!/bin/sh\nset -e\necho theirs\n"


class HookCommandCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.state = self.tmp / "state"

    def _repo(self, name: str) -> Path:
        repo = self.tmp / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=CLEAN_ENV)
        return repo

    def _cli(self, *args: str, cwd: Path | None = None, stdin: str = "") -> subprocess.CompletedProcess:
        env = dict(
            CLEAN_ENV,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.tmp / "config"),
            COPYDESK_STATE_DIR=str(self.state),
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=str(cwd or self.tmp), capture_output=True, text=True, env=env, input=stdin,
        )

    def _hook(self, repo: Path) -> Path:
        return repo / ".git" / "hooks" / "commit-msg"

    def _cli_tty(self, *args: str, cwd: Path, keys: list[bytes]) -> tuple[int, str]:
        """Drive the raw-mode prompts through a pty.

        `tty.setraw` uses TCSAFLUSH, which discards input queued before the
        mode change, so each key goes out only after the question has printed
        and the output has stalled. Enter accepts the default; down then
        Enter moves to No.
        """
        env = dict(
            CLEAN_ENV,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.tmp / "config"),
            COPYDESK_STATE_DIR=str(self.state),
        )
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            stdin=slave, stdout=slave, stderr=slave, cwd=str(cwd), env=env, close_fds=True,
        )
        os.close(slave)
        output = b""
        pending = list(keys)
        stalls = 0
        while True:
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                stalls = 0
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                continue
            if proc.poll() is not None:
                break
            stalls += 1
            if pending:
                if stalls >= 2:  # output quiet for a second: a prompt is waiting
                    os.write(master, pending.pop(0))
                    stalls = 0
            elif stalls > 20:
                break  # a question we have no answer for would hang; fail instead
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            os.close(master)
            raise AssertionError(f"process still waiting for input; output so far:\n{output.decode('utf-8', 'replace')}")
        # Drain whatever the process wrote between the last read and exit.
        try:
            while True:
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                output += chunk
        except OSError:
            pass
        os.close(master)
        return proc.returncode, output.decode("utf-8", "replace")

    def _write_foreign_hook(self, repo: Path, content: str = FOREIGN_HOOK) -> Path:
        hook_file = self._hook(repo)
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(content, encoding="utf-8")
        hook_file.chmod(0o755)
        return hook_file

    def _entries(self) -> list[dict]:
        path = self.state / "hooks.json"
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["repositories"]


class AddTests(HookCommandCase):
    def test_add_writes_the_hook_and_records_the_path(self) -> None:
        repo = self._repo("one")
        result = self._cli("hook", "add", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(hook.HOOK_MARKER, self._hook(repo).read_text(encoding="utf-8"))
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["git_dir"], str((repo / ".git").resolve()))
        self.assertEqual(entries[0]["state"], "installed")

    def test_add_in_a_plain_directory_says_so(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        result = self._cli("hook", "add", cwd=plain)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a git repository", result.stdout)
        self.assertEqual(self._entries(), [])

    def test_add_is_idempotent(self) -> None:
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        result = self._cli("hook", "add", cwd=repo)
        self.assertEqual(result.returncode, 0)
        self.assertIn("already installed", result.stdout)
        self.assertEqual(len(self._entries()), 1)

    def test_registry_writes_leave_no_temp_files(self) -> None:
        # The write goes through a temporary file and a rename under the state
        # lock. A leftover .tmp means a crash path is leaving broken JSON risk.
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        names = [p.name for p in self.state.iterdir()]
        self.assertIn("hooks.json", names)
        self.assertIn(".hooks.lock", names)
        self.assertEqual([n for n in names if n.endswith(".tmp")], [])


class ListTests(HookCommandCase):
    def test_list_reports_then_prunes_a_deleted_repository(self) -> None:
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        result = self._cli("hook", "list")
        self.assertIn(str(repo), result.stdout)
        self.assertIn("installed", result.stdout)

        shutil.rmtree(repo)
        result = self._cli("hook", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(repo), result.stdout)
        # Pruning is persisted, not just hidden from the report.
        self.assertEqual(self._entries(), [])

    def test_list_with_no_registry_says_none(self) -> None:
        result = self._cli("hook", "list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no repositories", result.stdout.lower())


class RemoveTests(HookCommandCase):
    def test_remove_all_clears_everything_and_leaves_a_foreign_hook(self) -> None:
        ours_one = self._repo("one")
        ours_two = self._repo("two")
        foreign = self._repo("foreign")
        self._cli("hook", "add", cwd=ours_one)
        self._cli("hook", "add", cwd=ours_two)
        foreign_hook = self._write_foreign_hook(foreign)
        self._cli("hook", "add", cwd=foreign, stdin="2\n")  # declined the chain
        self.assertEqual(len(self._entries()), 3)

        result = self._cli("hook", "remove", "--all", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self._hook(ours_one).exists())
        self.assertFalse(self._hook(ours_two).exists())
        # The marker is the test: nothing in this file is CopyDesk's, so it
        # stays byte-identical.
        self.assertEqual(foreign_hook.read_text(encoding="utf-8"), FOREIGN_HOOK)
        self.assertEqual(self._entries(), [])

    def test_remove_forgets_the_repository(self) -> None:
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        result = self._cli("hook", "remove", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self._hook(repo).exists())
        self.assertTrue(self._hook(repo).parent.is_dir(), "git owns its hooks directory")
        self.assertEqual(self._entries(), [])

    def test_remove_uses_the_disk_when_the_registry_is_empty(self) -> None:
        # The registry is a hint, never the truth: a hook installed before the
        # registry existed is still removable.
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        (self.state / "hooks.json").unlink()
        result = self._cli("hook", "remove", cwd=repo, stdin="")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self._hook(repo).exists())

    def test_remove_reports_when_there_is_nothing(self) -> None:
        repo = self._repo("one")
        result = self._cli("hook", "remove", cwd=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no CopyDesk hook", result.stdout)

    def test_remove_on_a_chained_hook_strips_the_region_byte_identically(self) -> None:
        repo = self._repo("chain")
        hook_file = self._write_foreign_hook(repo)
        self._cli("hook", "add", "--yes", cwd=repo)
        self.assertIn(hook.BLOCK_START, hook_file.read_text(encoding="utf-8"))
        result = self._cli("hook", "remove", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), FOREIGN_HOOK)


class SharedHooksTests(HookCommandCase):
    """Two repositories with core.hooksPath pointing at one directory share a
    hook. That is two entries, one file, and removal asks first."""

    def setUp(self) -> None:
        super().setUp()
        self.shared_hooks = self.tmp / "shared-hooks"
        self.shared_hooks.mkdir()
        self.one = self._repo("one")
        self.two = self._repo("two")
        for repo in (self.one, self.two):
            subprocess.run(
                ["git", "config", "core.hooksPath", str(self.shared_hooks)],
                cwd=repo, check=True, env=CLEAN_ENV,
            )

    def test_add_says_the_hooks_directory_is_shared(self) -> None:
        self._cli("hook", "add", cwd=self.one)
        result = self._cli("hook", "add", cwd=self.two)
        self.assertIn("shared", result.stdout.lower())
        self.assertIn(str(self.one), result.stdout)
        # Different common git directories stay two entries.
        self.assertEqual(len(self._entries()), 2)

    def test_remove_names_who_would_lose_the_hook_and_defaults_to_no(self) -> None:
        self._cli("hook", "add", cwd=self.one)
        self._cli("hook", "add", cwd=self.two)
        hook_file = self.shared_hooks / "commit-msg"
        # An empty answer is the default, and the default here is no: the
        # person asked about one repository and the answer affects two.
        result = self._cli("hook", "remove", cwd=self.one, stdin="\n")
        self.assertIn(str(self.two), result.stdout)
        self.assertTrue(hook_file.is_file())
        # The control: an explicit yes removes it.
        result = self._cli("hook", "remove", cwd=self.one, stdin="1\n")
        self.assertFalse(hook_file.exists())


class WorktreeTests(HookCommandCase):
    def test_linked_worktrees_collapse_into_one_entry(self) -> None:
        repo = self._repo("one")
        (repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=CLEAN_ENV)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
            cwd=repo, check=True, env=CLEAN_ENV,
        )
        tree = self.tmp / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feat", str(tree)],
            cwd=repo, check=True, env=CLEAN_ENV,
        )
        self._cli("hook", "add", cwd=repo)
        self._cli("hook", "add", cwd=tree)
        # Both worktrees share one hooks directory and one common git
        # directory, so they are one entry, not two.
        self.assertEqual(len(self._entries()), 1)


class ChainingTests(HookCommandCase):
    def test_both_block_markers_carry_the_phrase(self) -> None:
        # The registry verifies an entry by searching the hook for this one
        # phrase. A block marked any other way reads as missing, and every
        # chained entry is pruned the first time anything looks.
        self.assertIn(hook.MARKER_PHRASE, hook.BLOCK_START)
        self.assertIn(hook.MARKER_PHRASE, hook.BLOCK_END)

    def test_a_foreign_hook_is_chained_only_after_a_yes(self) -> None:
        repo = self._repo("one")
        hook_file = self._write_foreign_hook(repo)
        result = self._cli("hook", "add", cwd=repo, stdin="2\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), FOREIGN_HOOK)
        self.assertEqual(self._entries()[0]["state"], "skipped")

        result = self._cli("hook", "add", cwd=repo, stdin="\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        content = hook_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(FOREIGN_HOOK))
        self.assertIn(hook.BLOCK_START, content)
        self.assertEqual(self._entries()[0]["state"], "chained")

    def test_a_chain_swallowed_by_an_exit_is_reported_unverified(self) -> None:
        # A hook ending in `exit 0` swallows anything appended after it. The
        # block is in place but never runs, which is not a success.
        repo = self._repo("swallow")
        self._write_foreign_hook(repo, "#!/bin/sh\necho theirs\nexit 0\n")
        result = self._cli("hook", "add", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("never reached", result.stdout)
        self.assertEqual(self._entries()[0]["state"], "unreachable")

    def test_a_hook_that_refuses_every_message_is_also_unverified(self) -> None:
        # The control for the probe's shape: it proves reachability rather
        # than refusal, so a commitlint-style hook that exits 1 on any message
        # reads the same as an exit 0 above the block.
        repo = self._repo("refuse")
        self._write_foreign_hook(repo, "#!/bin/sh\necho no >&2\nexit 1\n")
        result = self._cli("hook", "add", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self._entries()[0]["state"], "unreachable")

    def test_a_reachable_chain_is_verified(self) -> None:
        # The control for both tests above: without them, a probe that always
        # answered "not reached" would pass everything.
        repo = self._repo("fine")
        self._write_foreign_hook(repo)
        result = self._cli("hook", "add", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self._entries()[0]["state"], "chained")

    def test_a_non_shell_hook_is_never_appended_to(self) -> None:
        repo = self._repo("python")
        hook_file = self._write_foreign_hook(repo, "#!/usr/bin/env python3\nprint('theirs')\n")
        result = self._cli("hook", "add", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("not an sh, bash or zsh script", result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('theirs')\n")
        self.assertEqual(self._entries()[0]["state"], "skipped")


class ChainedBlockExecutionTests(HookCommandCase):
    """The appended block is the hook's last word, so it sets the exit status.

    A passing CopyDesk must exit 0 and preserve the foreign script's own
    status; only exit 1 from CopyDesk refuses the commit.
    """

    def _run_chained(
        self, foreign_body: str, stub_exit: int, copydesk: str | None = None
    ) -> subprocess.CompletedProcess:
        scratch = self.tmp / "run"
        scratch.mkdir(exist_ok=True)
        stub = scratch / f"stub{stub_exit}"
        stub.write_text(f"#!/bin/sh\nexit {stub_exit}\n", encoding="utf-8")
        stub.chmod(0o755)
        message = scratch / "message"
        message.write_text("fix: expire reset tokens after first use\n", encoding="utf-8")
        chained = scratch / f"chained-{stub_exit}-{abs(hash(foreign_body))}"
        chained.write_text(foreign_body + hook.CHAINED_BLOCK, encoding="utf-8")
        chained.chmod(0o755)
        return subprocess.run(
            [str(chained), str(message)],
            capture_output=True, text=True,
            env=dict(CLEAN_ENV, COPYDESK_BIN=copydesk or str(stub)),
        )

    def test_a_passing_copydesk_lets_the_commit_through(self) -> None:
        result = self._run_chained("#!/bin/sh\necho theirs\n", stub_exit=0)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refusal_still_refuses(self) -> None:
        result = self._run_chained("#!/bin/sh\necho theirs\n", stub_exit=1)
        self.assertEqual(result.returncode, 1)

    def test_an_internal_error_fails_open_and_says_so(self) -> None:
        result = self._run_chained("#!/bin/sh\necho theirs\n", stub_exit=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copydesk: exit 3", result.stderr)

    def test_the_foreign_scripts_own_refusal_survives_chaining(self) -> None:
        # The block reads the foreign script's status before running CopyDesk
        # and exits with it, so a failing foreign hook still fails.
        result = self._run_chained("#!/bin/sh\necho theirs\nfalse\n", stub_exit=0)
        self.assertNotEqual(result.returncode, 0)

    def test_an_internal_error_fails_open_under_set_e(self) -> None:
        # `set -e` in the host script ends it at the first failing command, and
        # the block inherits that. A status the block means to swallow would
        # refuse the commit instead, which is the opposite of failing open.
        result = self._run_chained(STRICT_HOOK, stub_exit=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copydesk: exit 3", result.stderr)

    def test_a_refusal_under_set_e_still_refuses(self) -> None:
        # The control: errexit safety must not cost the one refusal.
        result = self._run_chained(STRICT_HOOK, stub_exit=1)
        self.assertEqual(result.returncode, 1)

    def test_a_missing_copydesk_lets_the_commit_through(self) -> None:
        # No CopyDesk on the machine is not a reason to refuse a commit, and
        # the guard runs before the call so errexit sees nothing to exit on.
        absent = str(self.tmp / "run" / "absent-copydesk")
        result = self._run_chained(STRICT_HOOK, stub_exit=0, copydesk=absent)
        self.assertEqual(result.returncode, 0, result.stderr)


class StrippedBlockTests(HookCommandCase):
    """Removal strips the marked region and never deletes a foreign script."""

    def _record_chained(self, repo: Path, content: str) -> Path:
        hook_file = self._write_foreign_hook(repo, content)
        # `hook add` records a hook carrying the marker without rewriting it.
        result = self._cli("hook", "add", "--yes", cwd=repo)
        self.assertIn("already chained", result.stdout)
        return hook_file

    def test_an_indented_pasted_block_strips_cleanly(self) -> None:
        # _chain_instructions prints the block indented. A verbatim paste must
        # still strip, and the whitespace goes with it.
        indented = "\n".join(
            f"             {line}" if line else line
            for line in hook.CHAINED_BLOCK.rstrip("\n").split("\n")
        )
        content = FOREIGN_HOOK + indented + "\n"
        repo = self._repo("pasted")
        hook_file = self._record_chained(repo, content)
        result = self._cli("hook", "remove", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), FOREIGN_HOOK)
        self.assertEqual(self._entries(), [])

    def test_a_block_without_its_end_marker_keeps_the_file_and_the_entry(self) -> None:
        # The start marker is present but the region does not match. Deleting
        # here would take a script CopyDesk did not write with it.
        broken = FOREIGN_HOOK + hook.BLOCK_START + "\necho halfway\n"
        repo = self._repo("broken")
        hook_file = self._record_chained(repo, broken)
        result = self._cli("hook", "remove", "--yes", cwd=repo)
        self.assertIn("could not be located", result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), broken)
        self.assertEqual(len(self._entries()), 1)


class ScanTests(HookCommandCase):
    def test_scan_declined_leaves_no_hook_and_no_entry(self) -> None:
        self._repo("clone-a")
        self._repo("clone-b")
        # Nothing is a default: an empty answer selects nothing.
        result = self._cli("hook", "add", "--scan", str(self.tmp), stdin="\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("clone-a", result.stdout)
        self.assertEqual(self._entries(), [])
        self.assertEqual(list(self.tmp.rglob("commit-msg")), [])

    def test_scan_all_adds_every_repository_found_without_a_prompt(self) -> None:
        self._repo("clone-a")
        self._repo("clone-b")
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        # No stdin at all: --all must take every candidate without asking.
        result = self._cli("hook", "add", "--scan", str(self.tmp), "--all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(self._hook(self.tmp / "clone-a").is_file())
        self.assertTrue(self._hook(self.tmp / "clone-b").is_file())
        self.assertEqual(len(self._entries()), 2)
        # The scan goes one level deep and offers repositories only.
        self.assertNotIn("not-a-repo", result.stdout)

    def test_scan_yes_alone_still_shows_the_multiselect(self) -> None:
        # --yes skips the chaining question, never the selection. Declining
        # leaves no hook and no entry.
        self._repo("clone-a")
        self._repo("clone-b")
        result = self._cli("hook", "add", "--scan", str(self.tmp), "--yes", stdin="\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Add a commit-msg hook to which repositories?", result.stdout)
        self.assertEqual(self._entries(), [])
        self.assertEqual(list(self.tmp.rglob("commit-msg")), [])

    def test_all_without_scan_is_a_usage_error_and_writes_nothing(self) -> None:
        repo = self._repo("clone-a")
        result = self._cli("hook", "add", "--all", cwd=repo)
        self.assertEqual(result.returncode, 64)
        self.assertIn("--all needs --scan", result.stderr)
        self.assertFalse(self._hook(repo).exists())
        self.assertEqual(self._entries(), [])

    def test_scan_records_a_hook_already_carrying_the_marker(self) -> None:
        repo = self._repo("clone-a")
        canonical = (ROOT / "git-hooks" / "commit-msg").read_text(encoding="utf-8")
        hook_file = self._hook(repo)
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(canonical, encoding="utf-8")
        result = self._cli("hook", "add", "--scan", str(self.tmp), "--all")
        self.assertIn("already installed", result.stdout)
        # Recorded, not rewritten: the file is untouched.
        self.assertEqual(hook_file.read_text(encoding="utf-8"), canonical)
        self.assertEqual(len(self._entries()), 1)


class SetupRegistryTests(HookCommandCase):
    def setUp(self) -> None:
        super().setUp()
        (self.home / ".claude").mkdir()

    def test_setup_records_the_repository(self) -> None:
        repo = self._repo("one")
        result = self._cli("setup", "--defaults", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # `hook list` reports it without `hook add` ever running.
        listing = self._cli("hook", "list")
        self.assertIn(str(repo), listing.stdout)
        # The outro names where the hook went and the command for the others.
        self.assertIn("copydesk hook add", result.stdout)

    def test_setup_records_a_foreign_hook_as_skipped(self) -> None:
        repo = self._repo("one")
        self._write_foreign_hook(repo)
        result = self._cli("setup", "--defaults", "--yes", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self._hook(repo).read_text(encoding="utf-8"), FOREIGN_HOOK)
        self.assertEqual(self._entries()[0]["state"], "skipped")


class UninstallTests(HookCommandCase):
    def setUp(self) -> None:
        super().setUp()
        (self.home / ".claude").mkdir()
        self.one = self._repo("one")
        self.two = self._repo("two")
        self._cli("hook", "add", cwd=self.one)
        self._cli("hook", "add", cwd=self.two)

    def test_uninstall_asks_once_and_an_empty_answer_means_yes(self) -> None:
        returncode, output = self._cli_tty("uninstall", cwd=self.one, keys=[b"\n", b"\n"])
        self.assertEqual(returncode, 0, output)
        self.assertEqual(output.count("Remove the CopyDesk hook from"), 1)
        self.assertFalse(self._hook(self.one).exists())
        self.assertFalse(self._hook(self.two).exists())
        self.assertEqual(self._entries(), [])

    def test_uninstall_no_leaves_every_hook_and_prints_their_paths(self) -> None:
        # Down, then Enter: the second answer is No. Each keypress is sent on
        # its own, after the output stalls: _get_key re-enters raw mode with
        # TCSAFLUSH on every key, which discards anything queued ahead of it.
        returncode, output = self._cli_tty("uninstall", cwd=self.one, keys=[b"\n", b"\x1b[B", b"\n"])
        self.assertEqual(returncode, 0, output)
        self.assertFalse(self._hook(self.one).exists())
        self.assertTrue(self._hook(self.two).is_file())
        # It says how many remain, and the path it prints is enough to remove
        # the hook by hand once no CopyDesk command is available.
        self.assertIn("1 repository still holds", output)
        self.assertIn(str(self._hook(self.two)), output)
        self.assertIn("copydesk hook remove --all", output)

    def test_uninstall_yes_takes_every_recorded_repository(self) -> None:
        result = self._cli("uninstall", "--yes", cwd=self.one)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("other recorded", result.stdout)
        self.assertFalse(self._hook(self.two).exists())
        self.assertEqual(self._entries(), [])

    def test_uninstall_strips_a_chained_block_and_keeps_the_script(self) -> None:
        three = self._repo("three")
        hook_file = self._write_foreign_hook(three)
        self._cli("hook", "add", "--yes", cwd=three)
        result = self._cli("uninstall", "--yes", cwd=three)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), FOREIGN_HOOK)
        self.assertFalse(self._hook(self.one).exists())
        self.assertFalse(self._hook(self.two).exists())

    def test_uninstall_reports_a_block_it_could_not_strip(self) -> None:
        # The start marker is present but the region does not match, so the
        # script is left alone. Claiming the uninstall is complete over lines
        # still in someone else's hook is the failure this guards.
        three = self._repo("three")
        hook_file = self._write_foreign_hook(
            three, FOREIGN_HOOK + hook.BLOCK_START + "\necho halfway\n"
        )
        broken = hook_file.read_text(encoding="utf-8")
        result = self._cli("uninstall", "--yes", cwd=three)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("could not be located", result.stdout)
        self.assertNotIn("Uninstall complete", result.stdout)
        self.assertEqual(hook_file.read_text(encoding="utf-8"), broken)

    def test_uninstall_never_deletes_the_registry(self) -> None:
        # The registry must survive uninstall: `copydesk hook remove` still
        # works afterwards, which is what makes the printed paths optional.
        returncode, _ = self._cli_tty("uninstall", cwd=self.one, keys=[b"\n", b"\x1b[B", b"\n"])
        self.assertEqual(returncode, 0)
        self.assertTrue((self.state / "hooks.json").is_file())
        removed = self._cli("hook", "remove", "--all", "--yes")
        self.assertEqual(removed.returncode, 0)
        self.assertFalse(self._hook(self.two).exists())


class DeletedRepoTests(HookCommandCase):
    def test_a_deleted_repository_never_errors_any_command(self) -> None:
        gone = self._repo("gone")
        self._cli("hook", "add", cwd=gone)
        shutil.rmtree(gone)

        self.assertEqual(self._cli("hook", "list").returncode, 0)
        self.assertEqual(self._cli("hook", "remove", "--all", "--yes").returncode, 0)

        # The pruned entry stays pruned, and later commands never see it.
        other = self._repo("other")
        self.assertEqual(self._cli("hook", "add", cwd=other).returncode, 0)
        self.assertEqual(len(self._entries()), 1)
        (self.home / ".claude").mkdir()
        result = self._cli("uninstall", "--yes", cwd=other)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


class RegistrySweepTests(HookCommandCase):
    def test_the_session_sweeper_leaves_the_registry(self) -> None:
        # The registry shares a directory with retry session state, which a
        # blocking gate run sweeps by glob once it passes the TTL. Sweeping the
        # registry with it would empty `hook list` while the hooks stayed on
        # disk.
        repo = self._repo("one")
        self._cli("hook", "add", cwd=repo)
        registry = self.state / "hooks.json"
        session = self.state / "a-session-id.json"
        session.write_text('{"files": {}}\n', encoding="utf-8")
        stale = time.time() - linter.STATE_TTL_SECONDS - 60
        for path in (registry, session):
            os.utime(path, (stale, stale))

        linter._sweep_state(self.state, time.time())

        self.assertTrue(registry.is_file(), "the registry is not session state")
        self.assertFalse(session.exists(), "stale session state still goes")
        self.assertEqual(len(self._entries()), 1)
        self.assertIn(str(repo), self._cli("hook", "list").stdout)


class BrokenRegistryTests(HookCommandCase):
    def test_broken_json_reads_as_empty_with_a_warning_and_every_command_runs(self) -> None:
        self.state.mkdir(parents=True)
        (self.state / "hooks.json").write_text("{ not json", encoding="utf-8")

        listing = self._cli("hook", "list")
        self.assertEqual(listing.returncode, 0)
        self.assertIn("warning", listing.stderr.lower())
        self.assertIn("no repositories", listing.stdout.lower())

        repo = self._repo("one")
        result = self._cli("hook", "add", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(self._entries()), 1)
        self.assertEqual(self._cli("hook", "remove", "--all", "--yes").returncode, 0)


if __name__ == "__main__":
    unittest.main()
