#!/usr/bin/env python3
"""A prompt kit in the standard library.

No dependencies is a project rule and the wizard does not get an exemption.
Raw-mode arrow keys where the terminal allows, numbered input where it does
not, and --defaults for no terminal at all.
"""

from __future__ import annotations

import sys
from typing import NamedTuple, Optional, Sequence, TextIO

# The vocabulary Claude Code's own menus use. The verb order is fixed, and
# inapplicable keys drop out: a single-select never shows space.
_KEYS = {
    "select": ("up down navigate", "enter confirm", "esc back"),
    "multiselect": ("up down navigate", "space toggle", "enter confirm", "esc back"),
    "confirm": ("up down navigate", "enter confirm", "esc back"),
}


class Cancelled(Exception):
    """Ctrl+C, escape, or end of input. Nothing has been written."""


class Option(NamedTuple):
    label: str
    consequence: str
    available: bool = True


def key_bar(kind: str) -> str:
    return " - ".join(_KEYS[kind])


def is_interactive(stdin: Optional[TextIO] = None) -> bool:
    stream = stdin or sys.stdin
    try:
        if not stream.isatty():
            return False
        import termios  # noqa: F401
        return True
    except (AttributeError, ValueError, ImportError):
        return False


def _get_key(stdin: TextIO) -> str:
    """Read a single key or escape sequence in raw mode."""
    import select as select_mod
    import termios
    import tty

    fd = stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = stdin.read(1)
        if ch == "\x03":  # Ctrl+C
            raise Cancelled("Cancelled by user")
        if ch == "\x1b":  # Escape sequence
            r, _, _ = select_mod.select([stdin], [], [], 0.05)
            if not r:
                raise Cancelled("Cancelled by user")
            ch2 = stdin.read(1)
            if ch2 == "[":
                ch3 = stdin.read(1)
                if ch3 == "A":
                    return "up"
                elif ch3 == "B":
                    return "down"
                elif ch3 == "C":
                    return "right"
                elif ch3 == "D":
                    return "left"
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        return ch
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
    bar = key_bar("select")
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
    bar = key_bar("multiselect")
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
