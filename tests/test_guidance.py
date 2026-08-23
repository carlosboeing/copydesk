"""Ten ids, ten snippets, and a merge table the generator owns."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import guidance  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_ten_ids_ship(self) -> None:
        self.assertEqual(
            guidance.IDS,
            (
                "recommendations",
                "direction",
                "progress",
                "pushback",
                "alternatives",
                "assumptions",
                "estimates",
                "sources",
                "summary",
                "verification",
            ),
        )

    def test_every_id_is_one_lowercase_word(self) -> None:
        for name in guidance.IDS:
            self.assertTrue(name.isalpha() and name.islower(), name)

    def test_every_snippet_sits_in_its_budget(self) -> None:
        for name, snippet in guidance.SNIPPETS.items():
            words = len(snippet.split())
            self.assertGreaterEqual(words, 15, f"{name}: {words} words")
            self.assertLessEqual(words, 25, f"{name}: {words} words")


class MergeTests(unittest.TestCase):
    def test_one_id_renders_its_own_snippet(self) -> None:
        rendered = guidance.render({"progress": True})
        self.assertEqual(rendered, [guidance.SNIPPETS["progress"]])

    def test_progress_and_direction_render_one_line(self) -> None:
        rendered = guidance.render({"progress": True, "direction": True})
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0], guidance.MERGES[frozenset({"progress", "direction"})])

    def test_alternatives_and_recommendations_render_one_line(self) -> None:
        rendered = guidance.render({"alternatives": True, "recommendations": True})
        self.assertEqual(len(rendered), 1)
        self.assertIn("ranked", rendered[0])

    def test_an_off_id_renders_nothing(self) -> None:
        self.assertEqual(guidance.render({"progress": False}), [])

    def test_output_follows_registry_order(self) -> None:
        rendered = guidance.render({"sources": True, "recommendations": True})
        self.assertEqual(rendered[0], guidance.SNIPPETS["recommendations"])

    def test_an_unknown_id_is_ignored_rather_than_raising(self) -> None:
        self.assertEqual(guidance.render({"vibes": True}), [])


class CollapseMembersTests(unittest.TestCase):
    def test_a_member_snippet_drops_when_the_merge_is_present(self) -> None:
        merged = guidance.MERGES[frozenset({"recommendations", "alternatives"})]
        member = guidance.SNIPPETS["recommendations"]
        self.assertEqual(guidance.collapse_members([merged, member]), [merged])

    def test_a_later_merge_still_drops_an_earlier_member(self) -> None:
        merged = guidance.MERGES[frozenset({"recommendations", "alternatives"})]
        member = guidance.SNIPPETS["recommendations"]
        self.assertEqual(guidance.collapse_members([member, merged]), [merged])

    def test_a_lone_member_stays(self) -> None:
        member = guidance.SNIPPETS["recommendations"]
        self.assertEqual(guidance.collapse_members([member]), [member])
