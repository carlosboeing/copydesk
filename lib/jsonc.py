#!/usr/bin/env python3
"""Strip JSON comments without moving anything.

Config files may carry `//` and `/* */`. The loader blanks them before
parsing, replacing each stripped character with a space and keeping every
newline, so a JSONDecodeError still names the position the user sees.
"""

from __future__ import annotations


class UnterminatedComment(ValueError):
    """A comment or string that never closes. The file is malformed."""


def strip_comments(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False

    while index < length:
        char = text[index]

        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                out.append(" ")
                index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "*":
            out.append("  ")
            index += 2
            closed = False
            while index < length:
                if text[index] == "*" and index + 1 < length and text[index + 1] == "/":
                    out.append("  ")
                    index += 2
                    closed = True
                    break
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
            if not closed:
                # `{"version": 1} /*` would otherwise blank to valid JSON plus
                # spaces, so a typo would load as a working config.
                raise UnterminatedComment("unterminated block comment")
            continue

        out.append(char)
        index += 1

    if in_string:
        # json.loads would report this too, but with a position measured in
        # the blanked text rather than the file the user is looking at.
        raise UnterminatedComment("unterminated string")

    return "".join(out)
