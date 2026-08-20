"""The schema is generated from the registries, never hand-written."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import adapters  # noqa: E402
import config  # noqa: E402
import guidance  # noqa: E402
import instructions  # noqa: E402
import linter  # noqa: E402
import styles  # noqa: E402

SCHEMA_FILE = ROOT / "copydesk.schema.json"
PRESET = json.loads((ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))


class ConventionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    def test_draft_seven_with_a_full_id(self) -> None:
        self.assertEqual(self.schema["$schema"], "http://json-schema.org/draft-07/schema#")
        self.assertEqual(self.schema["$id"], "https://json.schemastore.org/copydesk.config.json")

    def test_every_property_carries_a_description(self) -> None:
        def walk(node, trail=""):
            for name, body in (node.get("properties") or {}).items():
                self.assertIn("description", body, f"{trail}.{name}")
                walk(body, f"{trail}.{name}")
        walk(self.schema)

    def test_the_root_is_tolerant(self) -> None:
        # $schema and future keys must never break an older install.
        self.assertNotEqual(self.schema.get("additionalProperties"), False)

    def test_nested_objects_refuse_unknown_keys(self) -> None:
        self.assertFalse(self.schema["properties"]["gate"]["additionalProperties"])
        self.assertFalse(self.schema["properties"]["telemetry"]["additionalProperties"])


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    def test_style_enum_matches_the_shelf(self) -> None:
        chat = self.schema["properties"]["channels"]["properties"]["chat"]
        self.assertEqual(tuple(chat["properties"]["style"]["enum"]), styles.STYLE_NAMES)

    def test_guidance_enum_matches_the_registry(self) -> None:
        chat = self.schema["properties"]["channels"]["properties"]["chat"]
        self.assertEqual(
            sorted(chat["properties"]["guidance"]["properties"]), sorted(guidance.IDS)
        )

    def test_severity_enum_matches_the_frozen_vocabulary(self) -> None:
        # rules.additionalProperties is False since every rule has its own
        # schema, so the enum is read from one of those instead.
        rule = self.schema["properties"]["rules"]["properties"]["sentence-length"]
        self.assertEqual(sorted(rule["properties"]["severity"]["enum"]), ["error", "off", "warn"])

    def test_every_rule_schema_carries_the_same_severity_enum(self) -> None:
        for rule_id, schema in self.schema["properties"]["rules"]["properties"].items():
            self.assertEqual(
                sorted(schema["properties"]["severity"]["enum"]), ["error", "off", "warn"], rule_id
            )

    def test_channel_names_match_the_config_defaults(self) -> None:
        self.assertEqual(
            sorted(self.schema["properties"]["channels"]["properties"]), sorted(config.CHANNEL_DEFAULTS)
        )

    def test_gate_retries_states_its_range(self) -> None:
        retries = self.schema["properties"]["gate"]["properties"]["retries"]
        self.assertEqual((retries["minimum"], retries["maximum"], retries["default"]), (1, 5, 3))

    def test_every_rule_has_its_own_schema(self) -> None:
        self.assertEqual(
            sorted(self.schema["properties"]["rules"]["properties"]), sorted(config.rule_ids(PRESET))
        )

    def test_a_metric_rule_exposes_its_thresholds(self) -> None:
        sentence = self.schema["properties"]["rules"]["properties"]["sentence-length"]["properties"]
        self.assertIn("max", sentence)
        self.assertIn("hardMax", sentence)

    def test_a_metric_rule_refuses_word_lists(self) -> None:
        sentence = self.schema["properties"]["rules"]["properties"]["sentence-length"]["properties"]
        self.assertNotIn("add", sentence)

    def test_a_threshold_typo_is_invalid(self) -> None:
        sentence = self.schema["properties"]["rules"]["properties"]["sentence-length"]
        self.assertFalse(sentence["additionalProperties"])


class AgreementTests(unittest.TestCase):
    """The loader cannot validate against the schema, so fixtures assert they agree."""

    def test_every_valid_fixture_loads(self) -> None:
        for path in (ROOT / "tests" / "fixtures" / "config-valid").glob("*.json"):
            with self.subTest(path.name):
                config.resolve(ROOT / "rules", None, user_path=path)

    def test_the_loader_rejects_every_shared_invalid_fixture(self) -> None:
        for path in (ROOT / "tests" / "fixtures" / "config-invalid").glob("*.json"):
            with self.subTest(path.name), self.assertRaises(config.ConfigError):
                config.resolve(ROOT / "rules", None, user_path=path)

    def test_the_loader_accepts_the_schema_only_fixtures(self) -> None:
        # Documented divergence, not an accident: these are the cases the
        # schema catches in the editor and the loader deliberately does not.
        for path in (ROOT / "tests" / "fixtures" / "config-schema-only").glob("*.json"):
            with self.subTest(path.name):
                config.resolve(ROOT / "rules", None, user_path=path)


class SchemaUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    def test_the_schema_declares_the_catalog_id(self) -> None:
        self.assertEqual(self.schema["$id"], instructions.SCHEMA_ID)

    def test_the_wizard_writes_a_url_that_exists_today(self) -> None:
        if not instructions.SCHEMASTORE_MERGED:
            self.assertNotEqual(instructions.SCHEMA_URL, instructions.SCHEMA_ID)
            self.assertIn("raw.githubusercontent.com", instructions.SCHEMA_URL)

    def test_flipping_the_flag_switches_the_url(self) -> None:
        # The switch is one constant, so it cannot be half-done.
        self.assertEqual(instructions.SCHEMA_URL == instructions.SCHEMA_ID,
                         instructions.SCHEMASTORE_MERGED)


if __name__ == "__main__":
    unittest.main()
