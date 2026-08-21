#!/usr/bin/env python3
"""A prompt kit in the standard library.

No dependencies is a project rule and the wizard does not get an exemption.
Raw-mode arrow keys where the terminal allows, numbered input where it does
not, and --defaults for no terminal at all.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, NamedTuple, Optional, Sequence, TextIO

# The vocabulary Claude Code's own menus use. The verb order is fixed, and
# inapplicable keys drop out: a single-select never shows space.
_KEYS = {
    "select": ("{nav} to navigate", "Enter to confirm", "Esc to go back"),
    "multiselect": ("{nav} to navigate", "Space to toggle", "Enter to confirm", "Esc to go back"),
    "confirm": ("{nav} to navigate", "Enter to confirm", "Esc to go back"),
}

# Arrow glyphs read as the keys they name, where the terminal can print them.
# A terminal under LANG=C cannot, and writing one there raises rather than
# degrading, so the bar falls back to words it can always encode.
_NAV_GLYPH, _NAV_PLAIN = "\u2191/\u2193", "up/down"
_SEP_GLYPH, _SEP_PLAIN = " \u00b7 ", " - "


class Cancelled(Exception):
    """Ctrl+C, escape, or end of input. Nothing has been written."""


class Option(NamedTuple):
    label: str
    consequence: str
    available: bool = True


def _encodable(stream: Optional[TextIO], text: str) -> bool:
    """Whether `stream` can print `text` without raising."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def key_bar(kind: str, stream: Optional[TextIO] = None) -> str:
    """The key hints for one kind of question, in one line."""
    stream = sys.stdout if stream is None else stream
    glyphs = _encodable(stream, _NAV_GLYPH + _SEP_GLYPH)
    nav = _NAV_GLYPH if glyphs else _NAV_PLAIN
    separator = _SEP_GLYPH if glyphs else _SEP_PLAIN
    return separator.join(part.format(nav=nav) for part in _KEYS[kind])


def is_interactive(stdin: Optional[TextIO] = None) -> bool:
    stream = stdin or sys.stdin
    try:
        if not stream.isatty():
            return False
        import termios  # noqa: F401
        return True
    except (AttributeError, ValueError, ImportError):
        return False


_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}

# The byte that ends a CSI sequence, per ECMA-48: anything from `@` to `~`.
# Parameters and intermediates come before it and are all below `@`.
_CSI_FINAL = frozenset(chr(c) for c in range(0x40, 0x7F))

UNKNOWN = "unknown"


def _decode_key(read_byte: Callable[[], str], more_waiting: Callable[[], bool]) -> str:
    """Name one keypress, given a way to read a byte and to ask for another.

    An arrow key arrives as three bytes: escape, `[`, then a letter. Escape
    pressed alone arrives as one. Telling them apart means reading the first
    byte, then asking whether more are waiting, which is what `more_waiting`
    answers.

    A sequence that is not an arrow returns `UNKNOWN` rather than "esc", and
    every caller ignores it. Home, End, Page Up, Shift+Tab, the function
    keys, a bracketed paste and a mouse click all arrive as escape sequences.
    Naming them "esc" cancelled the wizard, because "esc" means go back.

    Separated from `_get_key` so the naming can be tested without a terminal.
    """
    ch = read_byte()
    if ch == "\x03":  # Ctrl+C
        raise Cancelled("Cancelled by user")
    if ch == "\x1b":
        if not more_waiting():
            return "esc"  # Escape by itself.
        intro = read_byte()
        if intro == "[":
            # Read to the end of the sequence whatever it turns out to be, so
            # no trailing byte is left to be read as a keypress of its own.
            body = ""
            while True:
                b = read_byte()
                body += b
                if b in _CSI_FINAL:
                    break
            return _ARROWS[body] if body in _ARROWS else UNKNOWN
        if intro == "O":
            read_byte()  # SS3: one byte follows, and none of them navigate.
            return UNKNOWN
        return UNKNOWN
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    return ch


def _get_key(stdin: TextIO) -> str:
    """Read a single key or escape sequence in raw mode.

    Bytes come off the file descriptor rather than through `stdin.read`. A
    terminal hands over all three bytes of an arrow key at once, and a text
    wrapper pulls every one of them into its own buffer on the first read.
    `select` then looks at the descriptor, finds nothing waiting, and
    concludes that Escape was pressed by itself. Every arrow key cancelled
    the wizard. Staying at the descriptor leaves the remaining bytes where
    `select` can see them.
    """
    import select as select_mod
    import termios
    import tty

    fd = stdin.fileno()

    def read_byte() -> str:
        data = os.read(fd, 1)
        if not data:  # The terminal closed mid-question.
            raise Cancelled("Cancelled by user")
        # latin-1 maps every byte to a character and never raises. Only ASCII
        # keys are named below, so a stray byte becomes a character that no
        # branch matches, which is what should happen to it.
        return data.decode("latin-1")

    def more_waiting() -> bool:
        return bool(select_mod.select([fd], [], [], 0.05)[0])

    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return _decode_key(read_byte, more_waiting)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _select_raw(
    question: str,
    options: Sequence[Option],
    default_index: int,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    current = max(0, min(default_index, len(options) - 1))
    bar = key_bar("select", stdout)
    lines_rendered = 0

    stdout.write("\x1b[?25l")  # Hide cursor
    try:
        while True:
            # Clear previous render
            if lines_rendered > 0:
                stdout.write(f"\x1b[{lines_rendered}A")
                stdout.write("\x1b[0J")

            out_lines = [question]
            for i, opt in enumerate(options):
                cursor = ">" if i == current else " "
                marker = "(*)" if i == current else "( )"
                suffix = f" - {opt.consequence}" if opt.consequence else ""
                avail = "" if opt.available else " (unavailable)"
                out_lines.append(f"{cursor} {marker} {opt.label}{suffix}{avail}")
            out_lines.append("")
            out_lines.append(bar)

            stdout.write("\n".join(out_lines) + "\n")
            stdout.flush()
            lines_rendered = len(out_lines)

            key = _get_key(stdin)
            if key == "up":
                current = (current - 1) % len(options)
            elif key == "down":
                current = (current + 1) % len(options)
            elif key == "enter":
                if options[current].available:
                    return current
            elif key == "esc":
                raise Cancelled("Cancelled by user")
    finally:
        stdout.write("\x1b[?25h\n")  # Show cursor
        stdout.flush()


def _multiselect_raw(
    question: str,
    options: Sequence[Option],
    preselected: Sequence[int],
    stdin: TextIO,
    stdout: TextIO,
) -> list[int]:
    current = 0
    selected = set(preselected)
    bar = key_bar("multiselect", stdout)
    lines_rendered = 0

    stdout.write("\x1b[?25l")  # Hide cursor
    try:
        while True:
            if lines_rendered > 0:
                stdout.write(f"\x1b[{lines_rendered}A")
                stdout.write("\x1b[0J")

            out_lines = [question]
            for i, opt in enumerate(options):
                cursor = ">" if i == current else " "
                marker = "[x]" if i in selected else "[ ]"
                suffix = f" - {opt.consequence}" if opt.consequence else ""
                avail = "" if opt.available else " (unavailable)"
                out_lines.append(f"{cursor} {marker} {opt.label}{suffix}{avail}")
            out_lines.append("")
            out_lines.append(bar)

            stdout.write("\n".join(out_lines) + "\n")
            stdout.flush()
            lines_rendered = len(out_lines)

            key = _get_key(stdin)
            if key == "up":
                current = (current - 1) % len(options)
            elif key == "down":
                current = (current + 1) % len(options)
            elif key == "space":
                if options[current].available:
                    if current in selected:
                        selected.remove(current)
                    else:
                        selected.add(current)
            elif key == "enter":
                return sorted(selected)
            elif key == "esc":
                raise Cancelled("Cancelled by user")
    finally:
        stdout.write("\x1b[?25h\n")  # Show cursor
        stdout.flush()


def _select_numbered(
    question: str,
    options: Sequence[Option],
    default_index: int,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    stdout.write(f"{question}\n")
    for i, opt in enumerate(options, 1):
        suffix = f" - {opt.consequence}" if opt.consequence else ""
        avail = "" if opt.available else " (unavailable)"
        stdout.write(f"  {i}) {opt.label}{suffix}{avail}\n")
    default_display = f" [{default_index + 1}]" if 0 <= default_index < len(options) else ""
    prompt_line = f"Choice (1 to {len(options)}){default_display}: "
    while True:
        stdout.write(prompt_line)
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise Cancelled("End of input")
        line = line.strip()
        if not line:
            if 0 <= default_index < len(options) and options[default_index].available:
                return default_index
            stdout.write(f"Please enter a number from 1 to {len(options)}.\n")
            continue
        try:
            num = int(line)
            if 1 <= num <= len(options):
                idx = num - 1
                if not options[idx].available:
                    stdout.write(f"Option {num} is not available. Choose another.\n")
                    continue
                return idx
        except ValueError:
            pass
        stdout.write(f"Please enter a number from 1 to {len(options)}.\n")


def _multiselect_numbered(
    question: str,
    options: Sequence[Option],
    preselected: Sequence[int],
    stdin: TextIO,
    stdout: TextIO,
) -> list[int]:
    stdout.write(f"{question}\n")
    for i, opt in enumerate(options, 1):
        suffix = f" - {opt.consequence}" if opt.consequence else ""
        avail = "" if opt.available else " (unavailable)"
        stdout.write(f"  {i}) {opt.label}{suffix}{avail}\n")
    default_display = ""
    if preselected:
        default_str = ",".join(str(i + 1) for i in sorted(preselected) if 0 <= i < len(options))
        if default_str:
            default_display = f" [{default_str}]"
    prompt_line = f"Choice (comma-separated 1 to {len(options)}){default_display}: "
    while True:
        stdout.write(prompt_line)
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise Cancelled("End of input")
        line = line.strip()
        if not line:
            valid_pre = [i for i in preselected if 0 <= i < len(options) and options[i].available]
            return valid_pre
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if not parts:
            return []
        selected = []
        valid = True
        for p in parts:
            try:
                num = int(p)
                if 1 <= num <= len(options):
                    idx = num - 1
                    if not options[idx].available:
                        stdout.write(f"Option {num} is not available.\n")
                        valid = False
                        break
                    if idx not in selected:
                        selected.append(idx)
                else:
                    valid = False
                    break
            except ValueError:
                valid = False
                break
        if valid:
            return selected
        stdout.write(f"Please enter numbers from 1 to {len(options)} separated by commas.\n")


def select(
    question: str,
    options: Sequence[Option],
    default_index: int = 0,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    if is_interactive(in_stream):
        return _select_raw(question, options, default_index, in_stream, out_stream)
    return _select_numbered(question, options, default_index, in_stream, out_stream)


def multiselect(
    question: str,
    options: Sequence[Option],
    preselected: Sequence[int] = (),
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> list[int]:
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    if is_interactive(in_stream):
        return _multiselect_raw(question, options, preselected, in_stream, out_stream)
    return _multiselect_numbered(question, options, preselected, in_stream, out_stream)


def confirm(
    question: str,
    default: bool = True,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> bool:
    options = [
        Option("Yes", ""),
        Option("No", ""),
    ]
    chosen = select(
        question,
        options,
        default_index=0 if default else 1,
        stdin=stdin,
        stdout=stdout,
    )
    return chosen == 0
