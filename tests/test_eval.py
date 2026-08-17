"""Regression tests for the evaluation scripts' shared exclusions."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EVALUATION = REPOSITORY_ROOT / "tools" / "plain-english" / "eval"
LIBRARY = REPOSITORY_ROOT / "tools" / "plain-english" / "lib"
sys.path.insert(0, str(LIBRARY))

import linter  # noqa: E402


def load_script(name: str):
    """Load a hyphenated evaluation script as a Python module."""
    path = EVALUATION / name
    specification = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if specification is None or specification.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class EvaluationExclusionTests(unittest.TestCase):
    def test_removing_a_linter_pattern_changes_both_script_measurements(self) -> None:
        """The scripts must call linter exclusions instead of copying their own rules."""
        count_jargon = load_script("count-jargon.py")
        measure_sentences = load_script("measure-sentences.py")
        text = "> Robust source material must not count.\nRobust authored prose must count.\n"

        normal_count = count_jargon.count_texts([text])
        normal_sentences = measure_sentences.sentences(text)

        with patch.object(linter, "_BLOCKQUOTE", re.compile(r"$^")):
            changed_count = count_jargon.count_texts([text])
            changed_sentences = measure_sentences.sentences(text)

        self.assertNotEqual(normal_count, changed_count)
        self.assertNotEqual(normal_sentences, changed_sentences)

    def test_since_accepts_a_date_and_filters_both_transcript_readers(self) -> None:
        """A date-only cutoff must keep post-change transcript records."""
        count_jargon = load_script("count-jargon.py")
        measure_sentences = load_script("measure-sentences.py")
        records = [
            {"type": "assistant", "timestamp": "2026-08-16T23:59:59Z", "message": {"content": [{"type": "text", "text": "old"}]}},
            {"type": "assistant", "timestamp": "2026-08-17T00:00:00Z", "message": {"content": [{"type": "text", "text": "new"}]}},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "session.jsonl"
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            cutoff = count_jargon.parse_timestamp("2026-08-17")

            self.assertEqual(list(count_jargon.texts(temporary, "chat", cutoff)), ["new"])
            self.assertEqual(list(measure_sentences.transcript_texts(str(transcript), "chat", cutoff)), ["new"])


if __name__ == "__main__":
    unittest.main()
