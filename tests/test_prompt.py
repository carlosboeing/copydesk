"""The prompt kit, driven through pipes rather than a terminal."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import prompt  # noqa: E402

OPTIONS = [
    prompt.Option("Short and direct", "answer first, one reason, stop", True),
    prompt.Option("More explanatory", "defines terms, adds context", True),
    prompt.Option("Thorough", "full reasoning, every step shown", True),
]


class KeyBarTests(unittest.TestCase):
    def test_a_single_select_bar_omits_space(self) -> None:
        self.assertEqual(prompt.key_bar("select"), "up down navigate - enter confirm - esc back")

    def test_a_multiselect_bar_includes_space(self) -> None:
        self.assertIn("space toggle", prompt.key_bar("multiselect"))

    def test_the_verb_order_is_fixed(self) -> None:
        bar = prompt.key_bar("multiselect")
        self.assertLess(bar.index("navigate"), bar.index("toggle"))
        self.assertLess(bar.index("toggle"), bar.index("confirm"))
        self.assertLess(bar.index("confirm"), bar.index("back"))


class FallbackTests(unittest.TestCase):
    def test_a_numbered_choice_is_read(self) -> None:
        chosen = prompt.select("Pick one", OPTIONS, default_index=0, stdin=io.StringIO("2\n"), stdout=io.StringIO())
        self.assertEqual(chosen, 1)

    def test_an_empty_line_takes_the_default(self) -> None:
        chosen = prompt.select("Pick one", OPTIONS, default_index=2, stdin=io.StringIO("\n"), stdout=io.StringIO())
        self.assertEqual(chosen, 2)

    def test_an_out_of_range_answer_is_asked_again(self) -> None:
        out = io.StringIO()
        chosen = prompt.select("Pick one", OPTIONS, default_index=0, stdin=io.StringIO("9\n2\n"), stdout=out)
        self.assertEqual(chosen, 1)
        self.assertIn("1 to 3", out.getvalue())

    def test_the_default_is_shown_in_brackets(self) -> None:
        out = io.StringIO()
        prompt.select("Pick one", OPTIONS, default_index=1, stdin=io.StringIO("\n"), stdout=out)
        self.assertIn("[2]", out.getvalue())

    def test_a_multiselect_reads_a_comma_list(self) -> None:
        chosen = prompt.multiselect("Pick some", OPTIONS, preselected=[0], stdin=io.StringIO("1,3\n"), stdout=io.StringIO())
        self.assertEqual(chosen, [0, 2])

    def test_an_unavailable_option_cannot_be_chosen(self) -> None:
        options = [OPTIONS[0], prompt.Option("Antigravity CLI", "not found on this machine", False)]
        out = io.StringIO()
        chosen = prompt.multiselect("Pick some", options, preselected=[], stdin=io.StringIO("2\n1\n"), stdout=out)
        self.assertEqual(chosen, [0])

    def test_end_of_input_cancels_rather_than_crashing(self) -> None:
        with self.assertRaises(prompt.Cancelled):
            prompt.select("Pick one", OPTIONS, default_index=0, stdin=io.StringIO(""), stdout=io.StringIO())


if __name__ == "__main__":
    unittest.main()
