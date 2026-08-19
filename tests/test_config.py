"""Config discovery, the preset cascade, and every documented error path."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = REPOSITORY_ROOT / "lib"
sys.path.insert(0, str(LIBRARY))

import config  # noqa: E402
import linter  # noqa: E402


RULES_DIR = REPOSITORY_ROOT / "rules"


def write_json(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def test_project_config_is_found_by_walking_up(self) -> None:
        """A config at the repository root must cover a nested document."""
        write_json(self.root / "copydesk.config.json", {"version": 1})
        nested = self.root / "docs" / "guides"
        nested.mkdir(parents=True)
        document = nested / "guide.md"
        document.write_text("text\n", encoding="utf-8")

        found = config.project_config_path(document)
        self.assertEqual(found, self.root / "copydesk.config.json")

    def test_the_nearest_config_wins_over_a_higher_one(self) -> None:
        write_json(self.root / "copydesk.config.json", {"version": 1})
        nested = self.root / "docs"
        write_json(nested / "copydesk.config.json", {"version": 1})
        document = nested / "guide.md"
        document.write_text("text\n", encoding="utf-8")

        self.assertEqual(config.project_config_path(document), nested / "copydesk.config.json")

    def test_no_config_anywhere_returns_none(self) -> None:
        document = self.root / "guide.md"
        document.write_text("text\n", encoding="utf-8")
        self.assertIsNone(config.project_config_path(document))

    def test_user_config_follows_xdg_then_home(self) -> None:
        saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.root)
        try:
            self.assertIsNone(config.user_config_path())
            written = write_json(self.root / "copydesk" / "config.json", {"version": 1})
            self.assertEqual(config.user_config_path(), written)
        finally:
            if saved is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = saved


class ErrorPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.document = self.root / "guide.md"
        self.document.write_text("text\n", encoding="utf-8")

    def test_two_config_files_in_one_directory_are_an_error(self) -> None:
        """Not a merge, and not a first-wins choice. Format precedence was the bug."""
        write_json(self.root / "copydesk.config.json", {"version": 1})
        (self.root / "copydesk.config.yaml").write_text("version: 1\n", encoding="utf-8")

        with self.assertRaises(config.ConfigError) as caught:
            config.project_config_path(self.document)
        message = str(caught.exception)
        self.assertIn("two config files", message)
        self.assertIn("copydesk.config.json", message)
        self.assertIn("copydesk.config.yaml", message)

    def test_a_stray_yaml_file_is_an_error_never_the_winner(self) -> None:
        """A stray YAML file must not silently hide a valid JSON one elsewhere."""
        (self.root / "copydesk.config.yaml").write_text("version: 1\n", encoding="utf-8")

        with self.assertRaises(config.ConfigError) as caught:
            config.project_config_path(self.document)
        message = str(caught.exception)
        self.assertIn("copydesk.config.yaml", message)
        self.assertIn("JSON only", message)

    def test_an_unknown_version_is_rejected_loudly(self) -> None:
        """Without a usable version field no schema migration is possible."""
        path = write_json(self.root / "copydesk.config.json", {"version": 99})
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("99", str(caught.exception))

    def test_a_missing_version_is_rejected(self) -> None:
        path = write_json(self.root / "copydesk.config.json", {"rules": {}})
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("no version field", str(caught.exception))

    def test_malformed_json_names_the_position(self) -> None:
        path = self.root / "copydesk.config.json"
        path.write_text('{"version": 1,,}', encoding="utf-8")
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("not valid JSON", str(caught.exception))

    def test_an_unreadable_file_is_an_error_not_a_crash(self) -> None:
        path = write_json(self.root / "copydesk.config.json", {"version": 1})
        path.chmod(0)
        self.addCleanup(path.chmod, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(path, os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("cannot be read", str(caught.exception))

    def test_extends_naming_a_missing_preset_lists_what_exists(self) -> None:
        path = write_json(self.root / "copydesk.config.json", {"version": 1, "extends": "no-such-preset"})
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        message = str(caught.exception)
        self.assertIn("no-such-preset", message)
        self.assertIn("plain-english", message)

    def test_extends_of_the_wrong_type_is_an_error(self) -> None:
        path = write_json(self.root / "copydesk.config.json", {"version": 1, "extends": 7})
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("string or an array", str(caught.exception))

    def test_an_unknown_severity_is_an_error(self) -> None:
        path = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"banned-word": {"severity": "fatal"}}},
        )
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("fatal", str(caught.exception))

    def test_add_on_a_metric_rule_is_an_error(self) -> None:
        """add and remove are word lists. A metric rule has no word list."""
        path = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"sentence-length": {"add": ["nonsense"]}}},
        )
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve(RULES_DIR, self.document, user_path=None, project_path=path)
        self.assertIn("pattern rules only", str(caught.exception))


class CascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.document = self.root / "guide.md"
        self.document.write_text("text\n", encoding="utf-8")

    def tokens_for(self, preset: dict, rule_id: str) -> list[str]:
        out = []
        for block in preset["patterns"]:
            if block["id"] == rule_id:
                out.extend(t if isinstance(t, str) else t["phrase"] for t in block["tokens"])
        return out

    def test_three_layers_compose_in_the_documented_order(self) -> None:
        """Preset, then user, then project. The project file wins."""
        user = write_json(
            self.root / "user.json",
            {"version": 1, "rules": {"banned-word": {"add": ["synergy"], "remove": ["robust"]}}},
        )
        project = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"banned-word": {"add": ["ideate"], "remove": ["synergy"]}}},
        )

        resolved = config.resolve(RULES_DIR, self.document, user_path=user, project_path=project)
        tokens = self.tokens_for(resolved, "banned-word")

        self.assertIn("ideate", tokens, "the project file's add must apply")
        self.assertNotIn("robust", tokens, "the user file's remove must apply")
        self.assertNotIn("synergy", tokens, "the project file removes what the user file added")

    def test_word_lists_merge_rather_than_replace(self) -> None:
        """Replacement would make extending a preset require restating it."""
        baseline = self.tokens_for(config.load_preset_document(RULES_DIR, "plain-english"), "banned-word")
        project = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"banned-word": {"add": ["synergy"]}}},
        )
        resolved = config.resolve(RULES_DIR, self.document, user_path=None, project_path=project)
        tokens = self.tokens_for(resolved, "banned-word")

        self.assertEqual(len(tokens), len(baseline) + 1)
        for token in baseline:
            self.assertIn(token, tokens)

    def test_extends_accepts_a_string_and_an_array(self) -> None:
        for value in ("plain-english", ["plain-english"]):
            with self.subTest(extends=value):
                project = write_json(self.root / "copydesk.config.json", {"version": 1, "extends": value})
                resolved = config.resolve(RULES_DIR, self.document, user_path=None, project_path=project)
                self.assertEqual(resolved["id"], "plain-english")

    def test_severity_off_removes_the_rule_from_compilation(self) -> None:
        project = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"banned-word": {"severity": "off"}}},
        )
        resolved = config.resolve(RULES_DIR, self.document, user_path=None, project_path=project)
        compiled = linter.compile_patterns(resolved)
        self.assertEqual([p for p in compiled if p.check == "banned-word"], [])
        self.assertTrue([p for p in compiled if p.check == "soft-offer"], "other rules must survive")

    def test_the_config_vocabulary_maps_onto_the_internal_strings(self) -> None:
        """error blocks, warn reports, off disables. The engine keeps error and warning."""
        project = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"banned-word": {"severity": "warn"}}},
        )
        resolved = config.resolve(RULES_DIR, self.document, user_path=None, project_path=project)
        compiled = linter.compile_patterns(resolved)
        severities = {p.severity for p in compiled if p.check == "banned-word"}
        self.assertSetEqual(severities, {"warning"})

    def test_a_rule_parameter_reaches_the_resolved_preset(self) -> None:
        project = write_json(
            self.root / "copydesk.config.json",
            {"version": 1, "rules": {"sentence-length": {"max": 30}}},
        )
        resolved = config.resolve(RULES_DIR, self.document, user_path=None, project_path=project)
        self.assertEqual(resolved["rules"]["sentence-length"]["max"], 30)


class FailOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        linter._PRESET_CACHE.clear()
        linter._REPORTED_CONFIG_ERRORS.clear()

    def test_a_broken_config_reports_and_lets_the_document_through(self) -> None:
        """A gate that blocks on its own misconfiguration is worse than one that does not."""
        (self.root / "copydesk.config.json").write_text("{ not json", encoding="utf-8")
        document = self.root / "guide.md"
        document.write_text("This approach is robust.\n", encoding="utf-8")

        findings = linter.lint(document.read_text(encoding="utf-8"), path=document)

        self.assertTrue(findings, "the built-in preset must still apply")
        self.assertTrue(
            any(f.check == "banned-word" for f in findings),
            "falling back to the built-in preset means banned-word still fires",
        )

    def test_the_error_is_reported_once_rather_than_per_document(self) -> None:
        (self.root / "copydesk.config.json").write_text("{ not json", encoding="utf-8")
        document = self.root / "guide.md"
        document.write_text("text\n", encoding="utf-8")

        linter.lint("text", path=document)
        first = len(linter._REPORTED_CONFIG_ERRORS)
        linter.lint("text", path=document)
        self.assertEqual(len(linter._REPORTED_CONFIG_ERRORS), first)
        self.assertGreaterEqual(first, 1)


if __name__ == "__main__":
    unittest.main()
