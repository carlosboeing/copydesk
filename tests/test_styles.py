"""Four styles, one alias, and a floor no style can remove."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import styles  # noqa: E402


class ShelfTests(unittest.TestCase):
    def test_four_styles_ship(self) -> None:
        self.assertEqual(styles.STYLE_NAMES, ("plain", "general", "engineer", "editorial"))

    def test_every_style_has_a_preset_file(self) -> None:
        for name in styles.STYLE_NAMES:
            self.assertTrue((ROOT / "rules" / f"{name}.json").is_file(), name)

    def test_plain_english_resolves_to_plain(self) -> None:
        self.assertEqual(styles.preset_for("plain-english"), "plain")

    def test_an_unknown_style_names_the_shelf(self) -> None:
        with self.assertRaises(styles.UnknownStyle) as caught:
            styles.preset_for("casual")
        self.assertIn("editorial", str(caught.exception))

    def test_every_style_has_a_description(self) -> None:
        """The wizard reads one per style, so a missing key crashes Customize."""
        self.assertEqual(sorted(styles.DESCRIPTIONS), sorted(styles.STYLE_NAMES))

    def test_each_description_matches_its_preset(self) -> None:
        """The copy is deliberate; drifting from the preset is not."""
        for name in styles.STYLE_NAMES:
            preset = json.loads((ROOT / "rules" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(styles.DESCRIPTIONS[name], preset["description"], name)


class FloorTests(unittest.TestCase):
    def test_the_floor_rules_are_error_under_every_style(self) -> None:
        for name in styles.STYLE_NAMES:
            preset = json.loads((ROOT / "rules" / f"{name}.json").read_text(encoding="utf-8"))
            blocks = {block["id"]: block["severity"] for block in preset.get("patterns", [])}
            for rule_id in ("soft-offer", "orphan-pointer"):
                self.assertNotEqual(blocks.get(rule_id, "error"), "off", f"{name}/{rule_id}")

    def test_the_behavioural_floor_has_no_config_key(self) -> None:
        # Immune means immune: every floor clause is prose, not a setting.
        self.assertEqual(
            sorted(styles.FLOOR),
            [
                "answer-first",
                "closing-block",
                "precedence",
                "say-once",
                "structure-when-earned",
                "target-form",
            ],
        )
        for clause in styles.FLOOR.values():
            self.assertNotIn("severity", clause)


class InheritanceTests(unittest.TestCase):
    def test_each_style_extends_plain(self) -> None:
        for name in ("general", "engineer", "editorial"):
            preset = json.loads((ROOT / "rules" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(preset["extends"], "plain")

    def test_general_glosses_everything(self) -> None:
        preset = json.loads((ROOT / "rules" / "general.json").read_text(encoding="utf-8"))
        self.assertEqual(preset["rules"]["unglossed-term"]["severity"], "error")

    def test_editorial_flags_a_list_dominated_document(self) -> None:
        preset = json.loads((ROOT / "rules" / "editorial.json").read_text(encoding="utf-8"))
        self.assertEqual(preset["rules"]["list-dominated"]["severity"], "error")
