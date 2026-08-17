#!/usr/bin/env python3
"""Count jargon/AI-tell frequency in Claude's assistant text across local transcripts."""
import argparse
import collections
import glob
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


LIBRARY = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402

CANDIDATES = [
    # the one Carlos named
    "seam", "seams",
    # structural/architecture jargon
    "load-bearing", "blast radius", "escape hatch", "happy path", "first-class",
    "affordance", "primitive", "ergonomics", "surface area", "in-flight",
    "boundary", "contract", "invariant", "idempotent", "canonical", "orthogonal",
    "coupling", "cohesion", "abstraction layer", "indirection",
    # verby jargon
    "wire up", "wire it", "plumb", "thread through", "bake in", "baked in",
    "surface", "surfaced", "surfaces", "unpack", "tease apart", "carve out",
    "land", "lands", "landed", "ship", "ships", "shipped",
    # hedge/filler
    "it's worth noting", "worth noting", "that said", "the thing is",
    "in practice", "arguably", "effectively", "essentially", "fundamentally",
    "meaningfully", "materially", "non-trivial", "nontrivial",
    # AI-tells (existing ban list)
    "delve", "leverage", "utilize", "a testament to", "crucial", "pivotal",
    "robust", "comprehensive", "moreover", "furthermore", "additionally",
    "in conclusion", "seamless", "seamlessly",
    # tone words
    "elegant", "crisp", "clean", "sharp", "subtle", "nuance", "nuanced",
    "tension", "tradeoff", "trade-off", "deliberate", "deliberately",
    "genuinely", "honestly", "actually",
    # contrast construction
    "not just", "isn't just", "it's not",
]

def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def texts(root: str, stream: str, since: datetime | None = None):
    for path in glob.glob(f"{root}/**/*.jsonl", recursive=True):
        try:
            with open(path, errors="ignore") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    timestamp = parse_timestamp(rec.get("timestamp"))
                    if since is not None and (timestamp is None or timestamp < since):
                        continue
                    msg = rec.get("message") or {}
                    for block in msg.get("content") or []:
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
        except OSError:
            continue

def count_texts(source_texts):
    """Return the jargon counts for already-separated text in one stream."""
    total_words = 0
    counts = collections.Counter()
    for text in source_texts:
        low = linter.exclude_markdown(text).lower()
        total_words += len(re.findall(r"[a-z']+", low))
        for candidate in CANDIDATES:
            found = len(re.findall(r"(?<![a-z])" + re.escape(candidate) + r"(?![a-z])", low))
            if found:
                counts[candidate] += found
    return total_words, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="directory containing Claude Code transcript JSONL files")
    parser.add_argument("--stream", choices=("chat", "docs"), required=True, help="measure one stream; streams are never combined")
    parser.add_argument("--since", type=parse_timestamp, metavar="ISO-8601", help="include records at or after this timestamp")
    arguments = parser.parse_args(argv)

    blocks = list(texts(arguments.root, arguments.stream, arguments.since))
    total_words, counts = count_texts(blocks)
    print(f"stream: {arguments.stream}")
    print(f"text blocks: {len(blocks)}")
    print(f"total words: {total_words:,}")
    print(f"{'term':24} {'count':>7} {'per 10k words':>14}")
    print("-" * 48)
    for term, count in counts.most_common():
        if count < 3:
            continue
        print(f"{term:24} {count:>7} {count / max(total_words, 1) * 10000:>14.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
