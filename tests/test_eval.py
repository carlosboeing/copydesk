"""Regression tests for the evaluation scripts' shared exclusions."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVALUATION = REPOSITORY_ROOT / "eval"
LIBRARY = REPOSITORY_ROOT / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402


def load_script(name: str):
    """Load a hyphenated evaluation script as a Python module."""
    path = EVALUATION / name
    specification = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if specification is None or specification.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class EvaluationExclusionTests(unittest.TestCase):
    def test_removing_a_linter_pattern_changes_both_script_measurements(self) -> None:
        """The scripts must call linter exclusions instead of copying their own rules."""
        count_jargon = load_script("count-jargon.py")
        measure_sentences = load_script("measure-sentences.py")
        text = "> Robust source material must not count.\nRobust authored prose must count.\n"

        normal_count = count_jargon.count_texts([text])
        normal_sentences = measure_sentences.sentences(text)

        with patch.object(linter, "_BLOCKQUOTE", re.compile(r"$^")):
            changed_count = count_jargon.count_texts([text])
            changed_sentences = measure_sentences.sentences(text)

        self.assertNotEqual(normal_count, changed_count)
        self.assertNotEqual(normal_sentences, changed_sentences)

    def test_since_accepts_a_date_and_filters_both_transcript_readers(self) -> None:
        """A date-only cutoff must keep post-change transcript records."""
        count_jargon = load_script("count-jargon.py")
        measure_sentences = load_script("measure-sentences.py")
        records = [
            {"type": "assistant", "timestamp": "2026-08-16T23:59:59Z", "message": {"content": [{"type": "text", "text": "old"}]}},
            {"type": "assistant", "timestamp": "2026-08-17T00:00:00Z", "message": {"content": [{"type": "text", "text": "new"}]}},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "session.jsonl"
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            cutoff = count_jargon.parse_timestamp("2026-08-17")

            self.assertEqual(list(count_jargon.texts(temporary, "chat", cutoff)), ["new"])
            self.assertEqual(list(measure_sentences.transcript_texts(str(transcript), "chat", cutoff)), ["new"])

    def test_until_excludes_the_design_window_from_both_transcript_readers(self) -> None:
        """A date-window end must keep historical records without design-chat contamination."""
        count_jargon = load_script("count-jargon.py")
        measure_sentences = load_script("measure-sentences.py")
        self.assertIn("until", inspect.signature(count_jargon.texts).parameters)
        self.assertIn("until", inspect.signature(measure_sentences.transcript_texts).parameters)
        records = [
            {"type": "assistant", "timestamp": "2026-08-15T13:59:59Z", "message": {"content": [{"type": "text", "text": "historical"}]}},
            {"type": "assistant", "timestamp": "2026-08-15T14:00:00Z", "message": {"content": [{"type": "text", "text": "design"}]}},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "session.jsonl"
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            cutoff = count_jargon.parse_timestamp("2026-08-16T00:00:00+10:00")

            self.assertEqual(list(count_jargon.texts(temporary, "chat", until=cutoff)), ["historical"])
            self.assertEqual(list(measure_sentences.transcript_texts(str(transcript), "chat", until=cutoff)), ["historical"])


class TranscriptExtractionTests(unittest.TestCase):
    def test_extracts_visible_chat_and_markdown_by_turn_for_all_harnesses(self) -> None:
        """Dropping a visible response or Write/Edit input must change the extracted streams."""
        self.assertTrue((EVALUATION / "extract-transcripts.py").exists(), "the extractor must exist before it can extract any transcript")
        extractor = load_script("extract-transcripts.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude = root / "claude.jsonl"
            claude.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {"type": "user", "message": {"content": [{"type": "text", "text": "turn 1"}]}},
                        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "ignore"}, {"type": "text", "text": "Claude chat"}, {"type": "tool_use", "name": "Write", "input": {"file_path": "notes.md", "content": "Claude document"}}]}},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            codex = root / "codex.jsonl"
            codex.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Codex chat"}]}},
                        {"type": "response_item", "payload": {"type": "function_call", "name": "Write", "arguments": json.dumps({"file_path": "notes.md", "content": "Codex document"})}},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            kimi = root / "kimi.jsonl"
            kimi.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {"type": "context.append_loop_event", "event": {"type": "content.part", "turnId": "turn-1", "part": {"type": "think", "think": "ignore"}}},
                        {"type": "context.append_loop_event", "event": {"type": "content.part", "turnId": "turn-1", "part": {"type": "text", "text": "Kimi chat"}}},
                        {"type": "context.append_loop_event", "event": {"type": "tool.call", "turnId": "turn-1", "name": "Write", "args": {"file_path": "notes.md", "content": "Kimi document"}}},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(extractor.extract_file("claude", claude), {"chat": {1: ["Claude chat"]}, "docs": {1: ["Claude document"]}})
            self.assertEqual(extractor.extract_file("codex", codex), {"chat": {1: ["Codex chat"]}, "docs": {1: ["Codex document"]}})
            self.assertEqual(extractor.extract_file("kimi", kimi), {"chat": {1: ["Kimi chat"]}, "docs": {1: ["Kimi document"]}})

    def test_claude_tool_results_do_not_create_corpus_turns(self) -> None:
        """A streamed Claude turn has tool-result user records but no prompt record."""
        extractor = load_script("extract-transcripts.py")
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "turn-05.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "command output"}]}},
                        {"type": "assistant", "message": {"content": [{"type": "text", "text": "First answer"}]}},
                        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "more output"}]}},
                        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Second answer"}]}},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(extractor.extract_file("claude", transcript), {"chat": {5: ["First answer", "Second answer"]}, "docs": {}})


class CorpusRunnerTests(unittest.TestCase):
    def test_preflight_records_controls_and_condition_runs_require_confirmation(self) -> None:
        """Removing the confirmation guard could start the costly corpus with the wrong controls."""
        runner = EVALUATION / "run-corpus.sh"
        self.assertTrue(runner.exists(), "the corpus runner must exist before it can launch a condition")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / "settings"
            (settings / "claude").mkdir(parents=True)
            (settings / "codex").mkdir()
            (settings / "kimi").mkdir()
            (settings / "claude" / "settings.json").write_text(json.dumps({"model": "opus[1m]", "effortLevel": "high", "permissions": {"defaultMode": "auto"}}), encoding="utf-8")
            (settings / "codex" / "config.toml").write_text('model = "gpt-5.6-terra"\nmodel_reasoning_effort = "xhigh"\n', encoding="utf-8")
            (settings / "kimi" / "config.toml").write_text('default_model = "kimi-code/k3"\n[thinking]\neffort = "high"\n', encoding="utf-8")
            results = root / "results"

            preflight = subprocess.run([str(runner), "--preflight", "--settings-root", str(settings), "--results-root", str(results)], text=True, capture_output=True, check=False)
            self.assertEqual(preflight.returncode, 0, preflight.stderr)
            controls = json.loads((results / "controls.json").read_text(encoding="utf-8"))
            self.assertEqual(controls["claude"]["model"], "opus[1m]")
            self.assertEqual(controls["codex"]["effort"], "xhigh")
            self.assertEqual(controls["kimi"]["approval_mode"], "prompt mode auto-approves tool calls")

            refused = subprocess.run([str(runner), "--harness", "claude", "--condition", "A", "--results-root", str(results)], text=True, capture_output=True, check=False)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("--confirmed", refused.stderr)

    def test_runs_one_continuous_sequence_under_macos_bash(self) -> None:
        """Replacing the corpus collector with a Bash-4-only builtin must break this run."""
        runner = EVALUATION / "run-corpus.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            repository = root / "crossrev"
            repository.mkdir()
            (repository / ".git").write_text("gitdir: /temporary/git/worktrees/corpus\n", encoding="utf-8")
            home = root / "home"
            log = root / "claude-calls.log"
            settings = root / "settings"
            (settings / "claude").mkdir(parents=True)
            (settings / "codex").mkdir()
            (settings / "kimi").mkdir()
            (settings / "claude" / "settings.json").write_text(json.dumps({"model": "opus[1m]", "effortLevel": "high", "permissions": {"defaultMode": "auto"}}), encoding="utf-8")
            (settings / "codex" / "config.toml").write_text('model = "gpt-5.6-terra"\nmodel_reasoning_effort = "xhigh"\n', encoding="utf-8")
            (settings / "kimi" / "config.toml").write_text('default_model = "kimi-code/k3"\n[thinking]\neffort = "high"\n', encoding="utf-8")

            fake_git = binaries / "git"
            fake_git.write_text("#!/bin/sh\ncase \"$*\" in *'rev-parse --is-inside-work-tree'*) echo true ;; *'rev-parse HEAD'*) echo c72d978cb8beb815d2f00a8b901b6e24c69c8e7c ;; esac\n", encoding="utf-8")
            fake_uuidgen = binaries / "uuidgen"
            fake_uuidgen.write_text("#!/bin/sh\necho 11111111-1111-4111-8111-111111111111\n", encoding="utf-8")
            fake_claude = binaries / "claude"
            fake_claude.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        "mode=",
                        "id=",
                        "verbose=false",
                        "while [ \"$#\" -gt 0 ]; do",
                        "  case \"$1\" in",
                        "    --session-id) mode=start; id=\"$2\"; shift 2 ;;",
                        "    --resume) mode=resume; id=\"$2\"; shift 2 ;;",
                        "    --verbose) verbose=true; shift ;;",
                        "    *) shift ;;",
                        "  esac",
                        "done",
                        "if [ \"$verbose\" != true ]; then echo '--verbose required' >&2; exit 37; fi",
                        "mkdir -p \"$HOME/.claude/projects/test\"",
                        "printf '%s %s\\n' \"$mode\" \"$id\" >> \"$TEST_LOG\"",
                        "printf '%s\\n' '{\"type\":\"user\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"prompt\"}]}}' >> \"$HOME/.claude/projects/test/$id.jsonl\"",
                        "printf '%s\\n' '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"answer\"}]}}' >> \"$HOME/.claude/projects/test/$id.jsonl\"",
                        "printf '%s\\n' '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"answer\"}]}}'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            for executable in (fake_git, fake_uuidgen, fake_claude):
                executable.chmod(0o755)

            environment = {
                **__import__("os").environ,
                "HOME": str(home),
                "PATH": f"{binaries}:{__import__('os').environ['PATH']}",
                "TEST_LOG": str(log),
            }
            results = root / "results"
            completed = subprocess.run(
                ["/bin/bash", str(runner), "--harness", "claude", "--condition", "A", "--repo", str(repository), "--sequence", "01", "--runs", "1", "--confirmed", "--settings-root", str(settings), "--results-root", str(results)],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls[0], "start 11111111-1111-4111-8111-111111111111")
            self.assertEqual(calls[1:], ["resume 11111111-1111-4111-8111-111111111111"] * 9)
            self.assertTrue((results / "A" / "claude" / "01-implementation-dry-run" / "run-1" / "claude-session.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
