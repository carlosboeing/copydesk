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


def _utf8() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


def _ascii() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="ascii")


class KeyBarTests(unittest.TestCase):
    def test_a_single_select_bar_omits_space(self) -> None:
        self.assertEqual(
            prompt.key_bar("select", _utf8()),
            "\u2191/\u2193 to navigate \u00b7 Enter to confirm \u00b7 Esc to go back",
        )

    def test_a_multiselect_bar_includes_space(self) -> None:
        self.assertIn("Space to toggle", prompt.key_bar("multiselect", _utf8()))

    def test_a_terminal_that_cannot_print_arrows_gets_words(self) -> None:
        # Under LANG=C the glyphs raise rather than degrading, so the bar has
        # to ask before printing them.
        bar = prompt.key_bar("multiselect", _ascii())
        self.assertEqual(
            bar, "up/down to navigate - Space to toggle - Enter to confirm - Esc to go back"
        )
        bar.encode("ascii")  # The point of the fallback: this must not raise.

    def test_the_arrow_bar_is_what_a_utf8_terminal_gets(self) -> None:
        # The control for the test above. Without it the fallback could be
        # returned everywhere and both tests would still pass.
        self.assertIn("\u2191/\u2193", prompt.key_bar("multiselect", _utf8()))

    def test_the_verb_order_is_fixed(self) -> None:
        bar = prompt.key_bar("multiselect", _utf8())
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


class KeyNamingTests(unittest.TestCase):
    """`_decode_key`, given bytes rather than a terminal."""

    @staticmethod
    def _from(data: str):
        """Name the key that `data` spells, with bytes 2 and 3 always waiting."""
        pending = list(data)
        return prompt._decode_key(lambda: pending.pop(0), lambda: bool(pending))

    def test_the_four_arrows_are_named(self) -> None:
        self.assertEqual(self._from("\x1b[A"), "up")
        self.assertEqual(self._from("\x1b[B"), "down")
        self.assertEqual(self._from("\x1b[C"), "right")
        self.assertEqual(self._from("\x1b[D"), "left")

    def test_enter_and_space_are_named(self) -> None:
        self.assertEqual(self._from("\r"), "enter")
        self.assertEqual(self._from("\n"), "enter")
        self.assertEqual(self._from(" "), "space")

    def test_ctrl_c_cancels(self) -> None:
        with self.assertRaises(prompt.Cancelled):
            prompt._decode_key(lambda: "\x03", lambda: False)

    def test_an_unknown_sequence_is_ignored_rather_than_cancelling(self) -> None:
        # Every one of these arrives as an escape sequence. Reading them as a
        # bare Escape meant Home, or a stray mouse click, cancelled the wizard.
        for name, seq in [
            ("Home", "\x1b[H"),
            ("End", "\x1b[F"),
            ("Page Up", "\x1b[5~"),
            ("Page Down", "\x1b[6~"),
            ("Shift+Tab", "\x1b[Z"),
            ("F1", "\x1bOP"),
            ("bracketed paste", "\x1b[200~"),
        ]:
            with self.subTest(key=name):
                self.assertEqual(self._from(seq), prompt.UNKNOWN)

    def test_escape_alone_is_still_escape(self) -> None:
        # The control. If every sequence were ignored, going back would break.
        self.assertEqual(prompt._decode_key(lambda: "\x1b", lambda: False), "esc")


class RawTerminalTests(unittest.TestCase):
    """`_get_key` against a real pseudo-terminal.

    The naming tests above pass with or without the fix, because the defect
    was never in the naming. A terminal delivers all three bytes of an arrow
    key at once; reading the first through a text wrapper pulled the other
    two into its buffer, so the `select` that asks whether more are waiting
    looked at an empty descriptor and cancelled. Only a real terminal
    reproduces that, so only this test guards against it returning.
    """

    def _press(self, keys: bytes) -> str:
        import os
        import pty
        import select
        import time

        child = r'''
import sys
sys.path.insert(0, %r)
import prompt
try:
    sys.stderr.write("KEY=%%s" %% prompt._get_key(sys.stdin))
except prompt.Cancelled:
    sys.stderr.write("KEY=cancelled")
''' % str(Path(__file__).resolve().parents[1] / "lib")

        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - the child execs immediately
            os.execv(sys.executable, [sys.executable, "-c", child])
        time.sleep(0.3)
        os.write(fd, keys)
        out = b""
        deadline = time.time() + 5
        while time.time() < deadline:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        os.waitpid(pid, 0)
        text = out.decode(errors="replace")
        return text.split("KEY=", 1)[1].strip() if "KEY=" in text else text.strip()

    def setUp(self) -> None:
        try:
            import pty  # noqa: F401
        except ImportError:  # pragma: no cover - Windows
            self.skipTest("no pty on this platform")

    def test_one_arrow_keypress_navigates_rather_than_cancelling(self) -> None:
        self.assertEqual(self._press(b"\x1b[B"), "down")
        self.assertEqual(self._press(b"\x1b[A"), "up")

    def test_escape_alone_is_still_escape(self) -> None:
        # The control. Without it the test above could pass by naming every
        # sequence an arrow, which would break the documented esc-to-go-back.
        self.assertEqual(self._press(b"\x1b"), "esc")


if __name__ == "__main__":
    unittest.main()
