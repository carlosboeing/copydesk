#!/usr/bin/env python3
"""Mean and spread of sentence length for prose samples."""
import argparse
import glob
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


LIBRARY = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402

def strip_noise(t):
    return linter.exclude_markdown(t)

def sentences(t):
    out = []
    for s in re.split(r"(?<=[.!?])\s+", strip_noise(t)):
        w = re.findall(r"[A-Za-z0-9'’\-]+", s)
        if len(w) >= 3:
            out.append(len(w))
    return out

def report(name, text):
    L = sentences(text)
    if len(L) < 5:
        print(f"{name:44} (too few sentences: {len(L)})"); return
    mean = statistics.mean(L)
    sd = statistics.pstdev(L)
    over25 = sum(1 for n in L if n > 25)
    print(f"{name:44} n={len(L):>4}  mean={mean:5.1f}  sd={sd:5.1f}  "
          f">25w={over25:>3} ({over25/len(L)*100:4.1f}%)  max={max(L)}")

def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def transcript_texts(path: str, stream: str, since: datetime | None = None):
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("type") != "assistant":
                continue
            timestamp = parse_timestamp(record.get("timestamp"))
            if since is not None and (timestamp is None or timestamp < since):
                continue
            for block in (record.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if stream == "chat" and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        yield text
                if stream == "docs" and block.get("type") == "tool_use" and block.get("name") in {"Write", "Edit"}:
                    tool_input = block.get("input") or {}
                    document = tool_input.get("file_path")
                    if not isinstance(document, str) or not document.endswith(".md"):
                        continue
                    field = "content" if block["name"] == "Write" else "new_string"
                    text = tool_input.get(field, "")
                    if isinstance(text, str):
                        yield text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Markdown files or transcript JSONL files")
    parser.add_argument("--stream", choices=("chat", "docs"), required=True, help="measure one stream; streams are never combined")
    parser.add_argument("--since", type=parse_timestamp, metavar="ISO-8601", help="include transcript records at or after this timestamp")
    arguments = parser.parse_args(argv)

    print(f"stream: {arguments.stream}")
    for path in arguments.paths:
        if path.endswith(".jsonl"):
            blocks = list(transcript_texts(path, arguments.stream, arguments.since))
            report(f"[{path}, {arguments.stream}]", "\n\n".join(blocks))
        elif arguments.stream == "docs":
            report(Path(path).name, Path(path).read_text(encoding="utf-8", errors="ignore"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
