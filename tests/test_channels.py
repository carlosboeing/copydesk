"""Path to action, and path to channel. Both are decided here, never by a model."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import channels  # noqa: E402


ROOT_DIR = "/repo"


def layer(index: int, **lists) -> list:
    """One layer's patterns, flattened to (layer, action, pattern, root)."""
    out = []
    for action in ("ignore", "warn", "block"):
        for pattern in lists.get(action, ()):
            out.append(channels.PathRule(index, action, pattern, ROOT_DIR))
    return out


BUILT_IN = layer(0, block=["**/*.md"])


def resolved(path_rules=None, **overrides) -> dict:
    base = {
        "pathRules": BUILT_IN if path_rules is None else path_rules,
        "channels": {
            "documents": {"enabled": True, "match": []},
            "reviews": {"enabled": True, "match": ["**/pr-body.md"]},
            "commits": {"enabled": True, "match": []},
            "chat": {"enabled": True, "match": []},
        },
    }
    base.update(overrides)
    return base


class GlobTests(unittest.TestCase):
    def test_a_double_star_crosses_directories(self) -> None:
        self.assertTrue(channels.matches("**/*.md", "docs/guides/setup.md"))

    def test_a_single_star_does_not_cross_a_separator(self) -> None:
        self.assertFalse(channels.matches("docs/*.md", "docs/guides/setup.md"))

    def test_a_bare_directory_pattern_matches_everything_under_it(self) -> None:
        self.assertTrue(channels.matches(".scratch/**", ".scratch/3-plans/x.md"))


class RelativeTests(unittest.TestCase):
    """Hook payloads carry absolute paths. Patterns are repository-relative."""

    def test_an_absolute_path_is_matched_relative_to_its_layer_root(self) -> None:
        self.assertEqual(channels.relative_to("/repo/docs/x.md", "/repo"), "docs/x.md")

    def test_a_path_outside_the_root_keeps_its_absolute_form(self) -> None:
        self.assertEqual(channels.relative_to("/elsewhere/x.md", "/repo"), "/elsewhere/x.md")

    def test_an_absolute_path_reaches_a_repository_relative_pattern(self) -> None:
        rules = BUILT_IN + layer(1, ignore=[".scratch/**"])
        self.assertEqual(channels.decide("/repo/.scratch/3-plans/x.md", resolved(rules)).action, "ignore")

    def test_a_file_outside_the_root_is_not_claimed_by_a_root_pattern(self) -> None:
        rules = BUILT_IN + layer(1, ignore=[".scratch/**"])
        self.assertEqual(channels.decide("/elsewhere/.scratch/x.md", resolved(rules)).action, "block")

    def test_a_worktree_path_resolves_against_its_own_root(self) -> None:
        rules = [channels.PathRule(0, "block", "**/*.md", "/repo"),
                 channels.PathRule(1, "ignore", "notes/**", "/repo/.worktrees/claude/feat")]
        target = "/repo/.worktrees/claude/feat/notes/x.md"
        self.assertEqual(channels.decide(target, resolved(rules)).action, "ignore")


class DecisionTests(unittest.TestCase):
    def test_a_markdown_file_blocks_by_default(self) -> None:
        decision = channels.decide("/repo/README.md", resolved())
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.channel, "documents")

    def test_a_non_markdown_file_is_ignored(self) -> None:
        self.assertEqual(channels.decide("/repo/main.py", resolved()).action, "ignore")

    def test_a_later_layer_beats_an_earlier_one(self) -> None:
        # The built-in blocks every Markdown file. A user layer naming
        # CHANGELOG.md as warn comes later, so it wins.
        rules = BUILT_IN + layer(1, warn=["CHANGELOG.md"])
        self.assertEqual(channels.decide("/repo/CHANGELOG.md", resolved(rules)).action, "warn")

    def test_a_bang_re_includes_within_its_own_list(self) -> None:
        rules = BUILT_IN + layer(1, ignore=[".scratch/**", "!.scratch/README.md"])
        self.assertEqual(channels.decide("/repo/.scratch/3-plans/x.md", resolved(rules)).action, "ignore")
        self.assertEqual(channels.decide("/repo/.scratch/README.md", resolved(rules)).action, "block")

    def test_the_strictest_reading_wins_inside_one_layer(self) -> None:
        rules = BUILT_IN + layer(1, ignore=["draft.md"], warn=["draft.md"], block=["draft.md"])
        self.assertEqual(channels.decide("/repo/draft.md", resolved(rules)).action, "ignore")

    def test_the_last_pattern_in_one_list_decides(self) -> None:
        rules = BUILT_IN + layer(1, ignore=["docs/**", "!docs/keep.md"])
        self.assertEqual(channels.decide("/repo/docs/keep.md", resolved(rules)).action, "block")

    def test_reviews_claims_before_documents(self) -> None:
        self.assertEqual(channels.decide("/repo/docs/pr-body.md", resolved()).channel, "reviews")

    def test_a_disabled_channel_does_not_claim(self) -> None:
        config = resolved()
        config["channels"]["reviews"]["enabled"] = False
        self.assertEqual(channels.decide("/repo/docs/pr-body.md", config).channel, "documents")

    def test_a_disabled_documents_channel_ignores_the_file(self) -> None:
        config = resolved()
        config["channels"]["documents"]["enabled"] = False
        self.assertEqual(channels.decide("/repo/notes.md", config).action, "ignore")

    def test_decide_without_path_rules_defaults_to_blocking_markdown(self) -> None:
        decision = channels.decide("/repo/doc.md", {})
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.channel, "documents")

    def test_decide_with_explicitly_empty_path_rules_returns_ignore(self) -> None:
        decision = channels.decide("/repo/doc.md", {"pathRules": []})
        self.assertEqual(decision.action, "ignore")
