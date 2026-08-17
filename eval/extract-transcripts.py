#!/usr/bin/env python3
"""Extract visible assistant chat and Markdown tool input from corpus transcripts."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any


Streams = dict[str, dict[int, list[str]]]
_TURN_NAME = re.compile(r"turn[-_](\d+)")


def _empty_streams() -> Streams:
    return {"chat": collections.defaultdict(list), "docs": collections.defaultdict(list)}


def _turn_from_path(path: Path) -> int:
    match = _TURN_NAME.search(path.name)
    return int(match.group(1)) if match else 1


def _append(streams: Streams, stream: str, turn: int, text: object) -> None:
    if isinstance(text, str) and text.strip():
        streams[stream][turn].append(text)


def _tool_text(name: object, tool_input: object) -> str | None:
    if name not in {"Write", "Edit"} or not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    if not isinstance(path, str) or not path.endswith(".md"):
        return None
    field = "content" if name == "Write" else "new_string"
    text = tool_input.get(field)
    return text if isinstance(text, str) else None


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _public(streams: Streams) -> dict[str, dict[int, list[str]]]:
    return {stream: {turn: texts for turn, texts in sorted(turns.items())} for stream, turns in streams.items()}


def _claude_prompt(content: object) -> bool:
    """Return whether a Claude user record starts a user-authored corpus turn."""
    if isinstance(content, str):
        return not content.lstrip().startswith("<task-notification>")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "text" for block in content)


def _extract_claude(records: list[dict[str, Any]], default_turn: int) -> Streams:
    streams = _empty_streams()
    turn = 0
    for record in records:
        if record.get("type") == "user":
            content = _mapping(record.get("message")).get("content")
            if _claude_prompt(content):
                turn += 1
            continue
        if record.get("type") != "assistant":
            continue
        active_turn = turn or default_turn
        content = _mapping(record.get("message")).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                _append(streams, "chat", active_turn, block.get("text"))
            if block.get("type") == "tool_use":
                _append(streams, "docs", active_turn, _tool_text(block.get("name"), block.get("input")))
    return streams


def _extract_codex(records: list[dict[str, Any]], default_turn: int) -> Streams:
    streams = _empty_streams()
    turns: dict[str, int] = {}
    active_turn = default_turn
    for record in records:
        if record.get("type") == "turn_context":
            turn_id = _mapping(record.get("payload")).get("turn_id")
            if isinstance(turn_id, str):
                turns.setdefault(turn_id, len(turns) + 1)
                active_turn = turns[turn_id]
            continue
        if record.get("type") != "response_item":
            continue
        payload = _mapping(record.get("payload"))
        if payload.get("type") == "message" and payload.get("role") == "assistant":
            for block in payload.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    _append(streams, "chat", active_turn, block.get("text"))
        name = payload.get("name")
        arguments = payload.get("arguments", payload.get("input"))
        _append(streams, "docs", active_turn, _tool_text(name, _mapping(arguments)))
    return streams


def _extract_kimi(records: list[dict[str, Any]], default_turn: int) -> Streams:
    streams = _empty_streams()
    turns: dict[str, int] = {}
    for record in records:
        if record.get("type") != "context.append_loop_event":
            continue
        event = _mapping(record.get("event"))
        turn_id = event.get("turnId")
        if not isinstance(turn_id, str):
            turn_id = "default"
        turns.setdefault(turn_id, len(turns) + 1 if turn_id != "default" else default_turn)
        active_turn = turns[turn_id]
        if event.get("type") == "content.part":
            part = _mapping(event.get("part"))
            if part.get("type") == "text":
                _append(streams, "chat", active_turn, part.get("text"))
        if event.get("type") == "tool.call":
            _append(streams, "docs", active_turn, _tool_text(event.get("name"), _mapping(event.get("args"))))
    return streams


def _extract_kimi_stdout(path: Path, default_turn: int) -> Streams:
    streams = _empty_streams()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("• "):
            _append(streams, "chat", default_turn, line[2:])
    return streams


def extract_file(harness: str, path: Path) -> dict[str, dict[int, list[str]]]:
    """Return each visible stream keyed by its one-based corpus turn."""
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                records.append(parsed)
    except json.JSONDecodeError:
        if harness == "kimi":
            return _public(_extract_kimi_stdout(path, _turn_from_path(path)))
        raise ValueError(f"{path} is not JSONL") from None

    default_turn = _turn_from_path(path)
    if harness == "claude":
        return _public(_extract_claude(records, default_turn))
    if harness == "codex":
        return _public(_extract_codex(records, default_turn))
    if harness == "kimi":
        return _public(_extract_kimi(records, default_turn))
    raise ValueError(f"unsupported harness: {harness}")


def _rows(streams: dict[str, dict[int, list[str]]]) -> dict[str, list[dict[str, object]]]:
    return {
        stream: [{"turn": turn, "text": text} for turn, texts in turns.items() for text in texts]
        for stream, turns in streams.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=("claude", "codex", "kimi"), required=True)
    parser.add_argument("--input", type=Path, required=True, help="a copied native session JSONL or Kimi prompt-mode stdout")
    parser.add_argument("--output", type=Path, required=True, help="JSON destination for separate chat and document streams")
    arguments = parser.parse_args(argv)

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(_rows(extract_file(arguments.harness, arguments.input)), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
