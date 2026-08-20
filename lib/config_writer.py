#!/usr/bin/env python3
"""Change one value in a config file without disturbing anything else.

The wizard writes explanatory comments into the config. A read-parse-dump
cycle would delete them all, so this edits the bytes in place.

Finding the value means walking the dotted path through the real object
structure, not matching the leaf name. A file listing `documents` before
`chat`, a `guidance` object that also has a `verbosity` key, or the string
"channels.chat.verbosity" sitting in a comment or a value all defeat a
name-only match, and all three are ordinary files.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import jsonc

_SCALAR = re.compile(r"(?:true|false|null|-?\d+(?:\.\d+)?)")


def _skip_string(text: str, index: int) -> int:
    """Index just past the string literal starting at `index`."""
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    raise ValueError("unterminated string")


def _skip_comment(text: str, index: int) -> int:
    """Index just past a comment at `index`, or `index` when there is none.

    The walker must know about comments, because this file format has them.
    A brace inside `// a decoy: { }` would otherwise close an object early,
    and a quoted key inside a comment would be selected as the real one.
    """
    if text[index] != "/" or index + 1 >= len(text):
        return index
    if text[index + 1] == "/":
        end = text.find("\n", index)
        return len(text) if end < 0 else end
    if text[index + 1] == "*":
        end = text.find("*/", index + 2)
        return len(text) if end < 0 else end + 2
    return index


def _object_span(text: str, start: int) -> tuple[int, int]:
    """(start, end) of the braced or bracketed value starting at `start`."""
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "/":
            after = _skip_comment(text, index)
            if after != index:
                index = after
                continue
        if char == '"':
            index = _skip_string(text, index)
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return start, index + 1
        index += 1
    raise ValueError("unbalanced object")


def _find_key(text: str, key: str, lo: int, hi: int) -> int:
    """Index of `key`'s value at the top level of the object spanning lo..hi."""
    needle = re.compile(r'"' + re.escape(key) + r'"\s*:\s*')
    index, depth = lo, 0
    while index < hi:
        char = text[index]
        if char == "/":
            after = _skip_comment(text, index)
            if after != index:
                index = after
                continue
        if char == '"':
            if depth == 1:
                match = needle.match(text, index)
                if match:
                    # A comment may sit between the colon and the value.
                    at = match.end()
                    while at < hi:
                        after = _skip_comment(text, at)
                        if after == at:
                            break
                        at = after
                        while at < hi and text[at] in " \t\r\n":
                            at += 1
                    return at
            index = _skip_string(text, index)
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        index += 1
    return -1


def value_span(text: str, dotted_key: str) -> Optional[tuple[int, int]]:
    """(start, end) of the value at `dotted_key`, or None when it is absent."""
    lo, hi = _object_span(text, text.index("{"))
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        at = _find_key(text, part, lo, hi)
        if at < 0 or text[at] != "{":
            return None
        lo, hi = _object_span(text, at)
    at = _find_key(text, parts[-1], lo, hi)
    if at < 0:
        return None
    if text[at] == '"':
        return at, _skip_string(text, at)
    if text[at] in "{[":
        return _object_span(text, at)
    match = _SCALAR.match(text, at)
    return (at, match.end()) if match else None


def _nested(dotted_key: str, value, base: dict) -> dict:
    """`base` with the dotted path set, creating intermediate objects."""
    node = base
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value
    return base


def set_value(path: Path, dotted_key: str, value) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        _write_atomic(path, json.dumps(_nested(dotted_key, value, {"version": 1}), indent=2) + "\n")
        return 0

    text = path.read_text(encoding="utf-8")
    span = value_span(text, dotted_key)
    if span is None:
        # No line to edit. Rewrite from the parsed object and say that
        # comments were lost, rather than losing them silently.
        parsed = json.loads(jsonc.strip_comments(text))
        _write_atomic(path, json.dumps(_nested(dotted_key, value, parsed), indent=2) + "\n")
        print(f"note: {path} was rewritten to add {dotted_key}; comments were not preserved")
        return 0

    _write_atomic(path, text[: span[0]] + json.dumps(value) + text[span[1] :])
    return 0


def _write_atomic(path: Path, content: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    )
    try:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
