"""Behavior tests for the CopyDesk CLI and PreToolUse wrapper."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

import linter  # noqa: E402
CLI = REPOSITORY_ROOT / "bin" / "copydesk"
GATE = REPOSITORY_ROOT / "hooks" / "gate.sh"
INSTALLER = REPOSITORY_ROOT / "install.sh"
FIXTURES = Path(__file__).parent / "fixtures"

_MODULE_TEMP_DIR: tempfile.TemporaryDirectory | None = None
_ORIG_STATE_DIR: str | None = None


def setUpModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    _MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
    _ORIG_STATE_DIR = os.environ.get("COPYDESK_STATE_DIR")
    os.environ["COPYDESK_STATE_DIR"] = _MODULE_TEMP_DIR.name


def tearDownModule() -> None:
    global _MODULE_TEMP_DIR, _ORIG_STATE_DIR
    if _ORIG_STATE_DIR is not None:
        os.environ["COPYDESK_STATE_DIR"] = _ORIG_STATE_DIR
    else:
        os.environ.pop("COPYDESK_STATE_DIR", None)
    if _MODULE_TEMP_DIR is not None:
        _MODULE_TEMP_DIR.cleanup()
        _MODULE_TEMP_DIR = None


class PlainEnglishCliTests(unittest.TestCase):
    def run_fixture(self, name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if "COPYDESK_STATE_DIR" not in env and _MODULE_TEMP_DIR is not None:
            env["COPYDESK_STATE_DIR"] = _MODULE_TEMP_DIR.name
        return subprocess.run(
            [sys.executable, str(CLI), str(FIXTURES / name)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def assert_blocking_check(self, fixture: str, check: str) -> None:
        """Removing this check must make its fixture test fail."""
        result = self.run_fixture(fixture)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, rf"(?m)^\d+:{re.escape(check)}:")

    def test_blocks_a_sentence_over_forty_words_with_a_line_number(self) -> None:
        """Removing the hard sentence cap must make this test fail."""
        result = self.run_fixture("sentence-length.md")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^1:sentence-length:")

    def test_blocks_each_remaining_mechanical_check(self) -> None:
        cases = {
            "long-sentence-rate.md": "long-sentence-rate",
            "paragraph-length.md": "paragraph-length",
            "orphan-pointer.md": "orphan-pointer",
            "soft-offer.md": "soft-offer",
            "banned-word.md": "banned-word",
            "contrast-construction.md": "contrast-construction",
            "announcing-opener.md": "announcing-opener",
            "idiom.md": "idiom",
            "nested-table.md": "nested-table",
            "verb-jargon.md": "verb-jargon",
        }
        for fixture, check in cases.items():
            with self.subTest(fixture=fixture):
                self.assert_blocking_check(fixture, check)

    def test_reports_advisory_checks_without_blocking(self) -> None:
        cases = {
            "sentence-length-warning.md": "sentence-length",
            "avg-sentence-length.md": "avg-sentence-length",
            "sentence-variation.md": "sentence-variation",
        }
        for fixture, check in cases.items():
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, rf"(?m)^\d+:{re.escape(check)}:")

    def test_stays_silent_for_a_known_good_document(self) -> None:
        """Adding a false-positive rule must make this test fail."""
        result = self.run_fixture("good.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_excludes_non_authored_markdown_content(self) -> None:
        fixtures = [
            "frontmatter.md",
            "fenced-code.md",
            "table.md",
            "quotation.md",
            "link-url.md",
            "heading.md",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_exempt_document_types_skip_file_level_statistics_only(self) -> None:
        for fixture in ("roadmap.md", "list-dominated.md"):
            with self.subTest(fixture=fixture):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

        self.assert_blocking_check("roadmap-banned-word.md", "banned-word")

    def test_reads_markdown_from_standard_input(self) -> None:
        """Removing stdin support must make pipeline callers fail."""
        result = subprocess.run(
            [sys.executable, str(CLI), "-"],
            input=(FIXTURES / "good.md").read_text(encoding="utf-8"),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


class PlainEnglishInstallTests(unittest.TestCase):
    def test_checkout_installer_places_a_working_cli_on_path(self) -> None:
        """Installing from a checkout makes the stable skill command work on PATH."""
        self.assertTrue(INSTALLER.is_file(), "checkout installer is missing")

        with tempfile.TemporaryDirectory() as temporary:
            bin_dir = Path(temporary) / "bin"
            installed = bin_dir / "copydesk"
            result = subprocess.run(
                ["/usr/bin/env", "bash", str(INSTALLER), "--yes", "--bin-dir", str(bin_dir)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(installed.is_symlink())
            self.assertEqual(installed.resolve(), CLI.resolve())

            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
            invocation = subprocess.run(
                ["copydesk", str(FIXTURES / "good.md")],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertEqual(invocation.stdout, "")


class PlainEnglishGateTests(unittest.TestCase):
    def run_gate(self, payload: dict[str, object], state_dir: str) -> subprocess.CompletedProcess[str]:
        """Run the real shell wrapper with isolated retry-state storage."""
        environment = os.environ.copy()
        environment["COPYDESK_STATE_DIR"] = state_dir
        return subprocess.run(
            ["/usr/bin/env", "bash", str(GATE)],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_blocks_a_bad_markdown_write_and_suggests_humanizer_for_ai_tells(self) -> None:
        """Removing Write reconstruction or the blocking exit must fail this test."""
        payload = {
            "session_id": "write-test",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/copydesk-write.md",
                "content": "Delve into the report before approving the release.",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            result = self.run_gate(payload, state_dir)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"(?m)^1:banned-word:")
        self.assertIn("/humanizer", result.stderr)

    def test_reconstructs_an_edit_before_linting(self) -> None:
        """Linting only new_string would let this resulting document bypass the gate."""
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "document.md"
            document.write_text("The report has a clear next action.\n", encoding="utf-8")
            payload = {
                "session_id": "edit-test",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(document),
                    "old_string": "clear",
                    "new_string": "robust",
                    "replace_all": False,
                },
            }
            result = self.run_gate(payload, str(Path(temporary) / "state"))

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"(?m)^1:banned-word:")

    def test_fails_open_for_unrelated_or_malformed_payloads(self) -> None:
        """A missing Write path must never stop an unrelated tool call."""
        payloads = [
            {"session_id": "other", "tool_name": "Read", "tool_input": {"file_path": "/tmp/a.md"}},
            {"session_id": "missing", "tool_name": "Write", "tool_input": {"content": "robust"}},
            {"session_id": "text", "tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "robust"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.md", "content": 42}},
        ]
        with tempfile.TemporaryDirectory() as state_dir:
            for payload in payloads:
                with self.subTest(payload=payload):
                    result = self.run_gate(payload, state_dir)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_allows_the_third_unresolved_attempt_with_a_content_hash_warning(self) -> None:
        """Resetting a failure streak on each revision would make this loop unbounded."""
        payload = {
            "session_id": "retry-test",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/tmp/copydesk-retry.md",
                "content": "The robust release needs a clearer description.",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            first = self.run_gate(payload, state_dir)
            second = self.run_gate(payload, state_dir)
            third = self.run_gate(payload, state_dir)

        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn("same content submitted 3 times", third.stderr)
        self.assertIn("sha256=", third.stderr)


class PatternBoundaryTests(unittest.TestCase):
    """Bounded patterns fire on the verb reading and stay quiet on the noun.

    The control for every silent assertion here is the first test: `carries`
    returns a hit through the same lint call that clears the clean reply.
    """

    def setUp(self) -> None:
        """Isolate discovery so the shipped preset judges, not the developer's."""
        self._config_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_home.cleanup)
        self._saved_config = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._config_home.name
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        if self._saved_config is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._saved_config

    def _phrases(self, text: str, check: str) -> set[str]:
        return {
            pattern.phrase
            for pattern in linter.RULE_PATTERNS
            if pattern.check == check and pattern.regex.search(text)
        }

    def test_the_control_fires_where_clean_text_stays_silent(self) -> None:
        self.assertIn("carries", self._phrases("The draft carries the answer.", "verb-jargon"))
        self.assertEqual(
            [f.check for f in linter.lint("Read the report before approving it.", channel="chat")],
            [],
        )

    def test_inflected_forms_fire_while_bare_forms_stay_legal(self) -> None:
        # lands/landed/landing are matched by the land entry that predates
        # this design; the rest are their own entries.
        for verb in ("carries", "carrying", "holds", "holding", "sits", "sitting",
                     "reaches", "travels", "rides"):
            with self.subTest(verb=verb):
                self.assertIn(verb, self._phrases(f"The module {verb} the state.", "verb-jargon"))
        for verb in ("lands", "landed", "landing"):
            with self.subTest(verb=verb):
                self.assertTrue(self._phrases(f"The module {verb} the state.", "verb-jargon"))
        legal = "Carry the flag, hold the line, sit tight, reach the goal, travel far, ride home."
        self.assertEqual(self._phrases(legal, "verb-jargon"), set())

    def test_surface_fires_as_a_verb_and_not_as_a_noun(self) -> None:
        for sentence in (
            "It surfaces later in the trace.",
            "This will surface in review.",
            "I'll surface the findings.",
            "to surface the cost",
            "They surfaced the risk early.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn("surface", self._phrases(sentence, "verb-jargon"))
        for sentence in ("the review surface", "a surface layer", "Surfaces in the diagram."):
            with self.subTest(sentence=sentence):
                self.assertNotIn("surface", self._phrases(sentence, "verb-jargon"))

    def test_shape_fires_bounded_and_the_plural_noun_stays_silent(self) -> None:
        for sentence in ("the shape of the fix", "that shape again", "its shape"):
            with self.subTest(sentence=sentence):
                self.assertIn("shape", self._phrases(sentence, "verb-jargon"))
        for sentence in ("shapes in the diagram", "reshape the API", "shaped like a ring"):
            with self.subTest(sentence=sentence):
                self.assertNotIn("shape", self._phrases(sentence, "verb-jargon"))

    def test_the_trap_family_fires_after_a_definite_article_at_a_clause_start(self) -> None:
        for sentence in ("The catch is the retry budget.", "Here is the trap.\nThe wrinkle: latency."):
            with self.subTest(sentence=sentence):
                self.assertIn("the trap", self._phrases(sentence, "verb-jargon"))
        for sentence in ("set a trap for it", "catch the exception", "tell me why"):
            with self.subTest(sentence=sentence):
                self.assertNotIn("the trap", self._phrases(sentence, "verb-jargon"))

    def test_spine_and_backbone_fire_after_a_possessive_or_the_article(self) -> None:
        for sentence in ("the spine of the document", "their backbone holds"):
            with self.subTest(sentence=sentence):
                self.assertIn("spine", self._phrases(sentence, "verb-jargon"))
        for sentence in ("a spine diagram", "backbone routers"):
            with self.subTest(sentence=sentence):
                self.assertNotIn("spine", self._phrases(sentence, "verb-jargon"))

    def test_the_new_banned_phrases_fire(self) -> None:
        for sentence, phrase in (
            ("The diff reads as a rewrite.", "reads as"),
            ("It reads like a patch.", "reads like"),
            ("Safe by construction.", "by construction"),
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(phrase, self._phrases(sentence, "banned-word"))

    def test_the_worth_pattern_covers_every_variant(self) -> None:
        for sentence in (
            "It's worth noting the cost.",
            "worth stating plainly",
            "worth saying once",
            "worth flagging now",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn("worth noting", self._phrases(sentence, "banned-word"))
        self.assertNotIn("worth noting", self._phrases("the worth of it", "banned-word"))

    def test_the_contrast_construction_matches_more_than_one_literal_shape(self) -> None:
        widened = "That is not a coincidence to work around with flags — it is a product difference"
        self.assertTrue(self._phrases(widened, "contrast-construction"))
        literal = "It's not just X — it's Y"
        self.assertTrue(self._phrases(literal, "contrast-construction"))
        self.assertEqual(
            self._phrases("This was not an oversight but rather a decision", "contrast-construction"),
            set(),
        )

    @staticmethod
    def _document(word_counts: list[int], dashes: int) -> tuple[str, int]:
        """One sentence per paragraph; a spaced dash adds exactly one token."""
        sentences = []
        for index, words in enumerate(word_counts):
            if words == 1:
                sentences.append("Ends.")
                continue
            body = f"Point {index} ends" if words == 3 else (
                f"Point {index} covers item {index} "
                + " ".join(f"w{index}x{n}" for n in range(words - 5))
            )
            sentences.append(body.strip() + ".")
        text = "\n\n".join(sentences)
        if dashes:
            head, _, tail = text.partition("\n\n")
            text = head + " —\n\n" + tail
        return text, len(text.split())

    def test_the_em_dash_rate_reports_once_above_the_threshold_only(self) -> None:
        # 999 prose words + 1 dash = 1000 tokens: 1.0 per thousand, silent.
        quiet, words = self._document([3] * 333, dashes=1)
        self.assertEqual(words, 1000)
        self.assertEqual([f.check for f in linter.lint(quiet) if f.check == "em-dash-rate"], [])

        # 249 prose words + 1 dash = 250 tokens: exactly 4.0, still silent.
        boundary, words = self._document([3] * 83, dashes=1)
        self.assertEqual(words, 250)
        self.assertEqual([f.check for f in linter.lint(boundary) if f.check == "em-dash-rate"], [])

        # 199 prose words + 1 dash = 200 tokens: 5.0 per thousand, reported once.
        loud, words = self._document([3] * 66 + [1], dashes=1)
        self.assertEqual(words, 200)
        found = [f for f in linter.lint(loud) if f.check == "em-dash-rate"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "warning")
        self.assertIn("per 1,000", found[0].excerpt)


if __name__ == "__main__":
    unittest.main()

class UnglossedTermTests(unittest.TestCase):
    """The rule orphan-pointer cannot express: a term used before it is defined."""

    def lint_text(self, text: str, path=None):
        return [f for f in linter.lint(text, path=path) if f.check == "unglossed-term"]

    def test_an_unglossed_first_use_is_flagged(self) -> None:
        found = self.lint_text("The team moved scheduling onto Kubernetes last spring.")
        self.assertEqual([f.excerpt.split(" \u2014")[0] for f in found], ["Kubernetes"])

    def test_the_rule_ships_as_a_warning_and_never_blocks(self) -> None:
        """No learning ships at 0.1.0, so the rule must not refuse a write."""
        found = self.lint_text("The team moved scheduling onto Kubernetes last spring.")
        self.assertTrue(found)
        for finding in found:
            self.assertEqual(finding.severity, "warning")

    def test_each_gloss_form_suppresses_the_finding(self) -> None:
        cases = {
            "appositive": "The team adopted Kubernetes, a container orchestrator, last spring.",
            "parenthetical": "The team adopted Kubernetes (a container orchestrator) last spring.",
            "definition": "The tool they picked, Kubernetes, is a container orchestrator.",
        }
        for form, text in cases.items():
            with self.subTest(gloss=form):
                self.assertEqual(self.lint_text(text), [])

    def test_a_sentence_initial_capital_is_not_a_term(self) -> None:
        self.assertEqual(self.lint_text("Kubernetes was adopted by the team last spring."), [])

    def test_a_list_item_initial_capital_is_sentence_initial(self) -> None:
        """The 2026-08-19 scan flagged Step, Modify, Create, Run and Add as terms."""
        text = "- Step through the queue.\n- Modify the record.\n- Create the namespace.\n- Run the batch.\n- Add the label.\n"
        self.assertEqual(self.lint_text(text), [])

    def test_a_capital_after_a_bold_marker_is_sentence_initial(self) -> None:
        """_SENTENCE_SPLIT does not split across a bold marker, so position zero lies."""
        self.assertEqual(self.lint_text("**Answer first.** **This** is the shape."), [])

    def test_the_masking_sentinels_are_never_terms(self) -> None:
        """exclude_markdown emits CODESPAN and URL, and both look exactly like terms."""
        text = "The team ran `Kubernetes` from https://example.com/runbook every morning.\n"
        found = {f.excerpt.split(" \u2014")[0] for f in self.lint_text(text)}
        self.assertNotIn("CODESPAN", found)
        self.assertNotIn("URL", found)

    def test_code_links_and_headings_are_skipped(self) -> None:
        for label, text in {
            "code": "The team adopted `Kubernetes` for scheduling.",
            "link": "The team adopted [it](https://Kubernetes.io) for scheduling.",
            "heading": "# The team adopted Kubernetes",
        }.items():
            with self.subTest(location=label):
                self.assertEqual(self.lint_text(text), [])

    def test_a_second_use_after_a_glossed_first_use_passes(self) -> None:
        text = (
            "The team adopted Kubernetes (a container orchestrator) last spring.\n"
            "The Kubernetes cluster grew to forty nodes by autumn.\n"
        )
        self.assertEqual(self.lint_text(text), [])

    def test_a_sentence_initial_first_use_counts_as_the_first_use(self) -> None:
        """Otherwise the term's next mid-sentence use is flagged after the reader met it."""
        text = (
            "Kubernetes is a container orchestrator the team adopted.\n"
            "The Kubernetes cluster grew to forty nodes by autumn.\n"
        )
        self.assertEqual(self.lint_text(text), [])

    def test_a_single_letter_is_never_a_term(self) -> None:
        """The pronoun I, a list label, and an initial cannot carry a gloss."""
        found = {f.excerpt.split(" \u2014")[0] for f in self.lint_text("The plan says I should pick option B here.")}
        self.assertNotIn("I", found)
        self.assertNotIn("B", found)

    def test_the_shipped_vocabulary_suppresses_universal_terms(self) -> None:
        text = "The team stored the JSON in Postgres and served it over HTTP from macOS.\n"
        self.assertEqual(self.lint_text(text), [])

    def test_a_project_vocabulary_suppresses_project_terms(self) -> None:
        """CrossRev needs no gloss inside its own repository and needs one in a blog post."""
        text = "The team wired CopyDesk into CrossRev during the migration.\n"
        without = self.lint_text(text)
        self.assertTrue(without, "with no project config both terms are unknown")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "copydesk.config.json").write_text(
                json.dumps({
                    "version": 1,
                    "rules": {"unglossed-term": {"vocabulary": {"add": ["CopyDesk", "CrossRev"]}}},
                }),
                encoding="utf-8",
            )
            document = root / "note.md"
            document.write_text(text, encoding="utf-8")
            linter._PRESET_CACHE.clear()
            self.assertEqual(self.lint_text(text, path=document), [])
        linter._PRESET_CACHE.clear()

    def test_severity_off_silences_the_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "copydesk.config.json").write_text(
                json.dumps({"version": 1, "rules": {"unglossed-term": {"severity": "off"}}}),
                encoding="utf-8",
            )
            document = root / "note.md"
            document.write_text("The team moved onto Kubernetes.\n", encoding="utf-8")
            linter._PRESET_CACHE.clear()
            self.assertEqual(self.lint_text(document.read_text(encoding="utf-8"), path=document), [])
        linter._PRESET_CACHE.clear()

    def test_the_rule_is_recorded_as_a_rule_never_as_a_pattern(self) -> None:
        """Recording it as a token list would mislead a port into treating a heuristic as data."""
        preset = json.loads((REPOSITORY_ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))
        self.assertIn("unglossed-term", preset["rules"])
        self.assertEqual([b for b in preset["patterns"] if b["id"] == "unglossed-term"], [])

    def test_every_shipped_term_carries_a_recorded_reason(self) -> None:
        """A reader must be able to challenge one entry rather than the whole list."""
        preset = json.loads((REPOSITORY_ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))
        vocabulary = preset["rules"]["unglossed-term"]["vocabulary"]
        explained = {term for group in vocabulary["rationale"].values() for term in group}
        self.assertSetEqual(set(vocabulary["add"]), explained)


class CommandSurfaceTests(unittest.TestCase):
    """The frozen surface: commands, flags, environment variables and exit codes."""

    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.setdefault("COPYDESK_STATE_DIR", tempfile.mkdtemp())
        return subprocess.run(
            [sys.executable, str(CLI), *args], input=stdin, text=True, capture_output=True, env=env
        )

    def test_version_matches_the_version_file(self) -> None:
        """The tag, VERSION and --version must agree, so read the file here."""
        expected = (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), expected)

    def test_readme_project_status_names_the_current_version(self) -> None:
        """README's status line went four releases saying 0.2.0.

        Nothing read it, so nothing caught it. A release that bumps VERSION
        now fails here until the same commit set updates the line.
        """
        expected = (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"**`{expected}`, pre-1.0.**", readme)

    def test_check_accepts_paths(self) -> None:
        result = self.run_cli("check", str(FIXTURES / "good.md"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_accepts_standard_input(self) -> None:
        result = self.run_cli("check", "-", stdin="The design is robust.\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("banned-word", result.stdout)

    def test_a_bare_path_still_lints(self) -> None:
        """Every existing caller passes a path with no subcommand."""
        result = self.run_cli(str(FIXTURES / "good.md"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctor_reports_and_never_mutates(self) -> None:
        state = Path(tempfile.mkdtemp()) / "untouched"
        env = dict(os.environ, COPYDESK_STATE_DIR=str(state))
        result = subprocess.run(
            [sys.executable, str(CLI), "doctor"], text=True, capture_output=True, env=env
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for section in ("Preset", "Config", "State", "Hooks", "Harness registration"):
            self.assertIn(section, result.stdout)
        self.assertFalse(state.exists(), "doctor must not create the state directory")

    def test_doctor_rejects_options(self) -> None:
        self.assertEqual(self.run_cli("doctor", "--json").returncode, 64)

    def test_every_reserved_subcommand_refuses_clearly(self) -> None:
        """Reserving them stops a 0.1.0 flag claiming a word a subcommand needs."""
        for name in ("learn", "fix", "import"):
            with self.subTest(subcommand=name):
                result = self.run_cli(name)
                self.assertEqual(result.returncode, 64)
                self.assertIn("reserved", result.stderr)

    def test_no_arguments_prints_usage_and_returns_sixty_four(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 64)
        self.assertIn("usage:", result.stderr)

    def test_the_usage_text_names_every_subcommand(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for name in ("check", "doctor", "stats", "report", "setup", "uninstall", "--version"):
            self.assertIn(name, result.stdout)

    def test_stats_and_report_are_unchanged(self) -> None:
        self.assertEqual(self.run_cli("stats").returncode, 0)
        self.assertEqual(self.run_cli("stats", "--json").returncode, 0)
        self.assertEqual(self.run_cli("stats", "--since").returncode, 64)

    def test_the_casing_rule_holds_in_printed_output(self) -> None:
        """Printed output takes CopyDesk; the command and its arguments stay lowercase."""
        stats = self.run_cli("stats").stdout
        self.assertIn("CopyDesk", stats)
        doctor = self.run_cli("doctor").stdout
        self.assertIn("CopyDesk", doctor)
        self.assertIn("copydesk", doctor)


class ThresholdTests(unittest.TestCase):
    """A configured threshold must change findings, not just the resolved dict."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        linter._PRESET_CACHE.clear()

    def _config(self, body: str) -> Path:
        (self.root / "copydesk.config.json").write_text(body, encoding="utf-8")
        doc = self.root / "doc.md"
        doc.write_text("placeholder\n", encoding="utf-8")
        return doc

    def test_a_lowered_sentence_max_flags_a_short_sentence(self) -> None:
        doc = self._config(
            '{"version": 1, "rules": {"sentence-length": {"severity": "error", "max": 5, "hardMax": 6}}}'
        )
        findings = linter.lint("This particular sentence has exactly nine words in it.\n", path=doc)
        self.assertIn("sentence-length", [f.check for f in findings])

    def test_the_snake_case_alias_still_works(self) -> None:
        doc = self._config(
            '{"version": 1, "rules": {"sentence-length": {"severity": "error", "max": 5, "hard_max": 6}}}'
        )
        findings = linter.lint("This particular sentence has exactly nine words in it.\n", path=doc)
        self.assertIn("sentence-length", [f.check for f in findings])

    def test_a_raised_paragraph_max_stops_flagging(self) -> None:
        doc = self._config('{"version": 1, "rules": {"paragraph-length": {"maxSentences": 9}}}')
        body = " ".join(f"Sentence number {n} sits here." for n in range(6)) + "\n"
        self.assertNotIn("paragraph-length", [f.check for f in linter.lint(body, path=doc)])

    def test_the_configured_exemption_ratio_changes_the_exemption(self) -> None:
        doc = self._config('{"version": 1, "rules": {"list-dominated": {"exemptionRatio": 0.9}}}')
        body = "- one\n- two\n- three\n\n" + " ".join(
            f"Sentence {n} sits in this paragraph." for n in range(6)
        ) + "\n"
        # At 0.5 the document is list-dominated and paragraph-length is skipped.
        # At 0.9 it is not, so the paragraph rule runs again.
        self.assertIn("paragraph-length", [f.check for f in linter.lint(body, path=doc)])

    def test_list_dominated_reports_when_switched_on(self) -> None:
        doc = self._config('{"version": 1, "rules": {"list-dominated": {"severity": "error"}}}')
        body = "Intro line.\n\n- one\n- two\n- three\n- four\n"
        self.assertIn("list-dominated", [f.check for f in linter.lint(body, path=doc)])

    def test_list_dominated_stays_silent_at_its_default(self) -> None:
        doc = self._config('{"version": 1}')
        body = "Intro line.\n\n- one\n- two\n- three\n- four\n"
        self.assertNotIn("list-dominated", [f.check for f in linter.lint(body, path=doc)])

