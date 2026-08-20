"""Test in-place config edits and comments preservation."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import config_writer  # noqa: E402


class ConfigWriterSpanTests(unittest.TestCase):
    def test_reordered_four_channel_config(self) -> None:
        text = '{"channels": {"documents": {"verbosity": "high"}, "chat": {"verbosity": "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_decoy_verbosity_nested_inside_guidance(self) -> None:
        text = '{"channels": {"chat": {"guidance": {"verbosity": true}, "verbosity": "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_dotted_key_sitting_inside_string_value(self) -> None:
        text = '{"message": "channels.chat.verbosity = high", "channels": {"chat": {"verbosity": "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_absent_key(self) -> None:
        text = '{"channels": {"chat": {"style": "plain"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNone(span)

    def test_escaped_quote_followed_by_brace_inside_string(self) -> None:
        text = r'{"escaped": "foo\"}", "channels": {"chat": {"verbosity": "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_decoy_object_inside_line_comment(self) -> None:
        text = '{\n// {"channels": {"chat": {"verbosity": "high"}}}\n"channels": {"chat": {"verbosity": "low"}}\n}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_decoy_object_inside_block_comment(self) -> None:
        text = '{\n/* {"channels": {"chat": {"verbosity": "high"}}} */\n"channels": {"chat": {"verbosity": "low"}}\n}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_stray_brace_inside_comment(self) -> None:
        text = '{\n// a comment with a stray } brace\n"channels": {"chat": {"verbosity": "low"}}\n}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_comment_between_colon_and_value(self) -> None:
        text = '{"channels": {"chat": {"verbosity": /* comment */ "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')

    def test_double_slash_inside_url_in_string(self) -> None:
        text = '{"url": "https://example.com/api", "channels": {"chat": {"verbosity": "low"}}}'
        span = config_writer.value_span(text, "channels.chat.verbosity")
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(text[span[0] : span[1]], '"low"')


class ConfigWriterSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_set_value_on_existing_file(self) -> None:
        path = self.tmp / "config.json"
        path.write_text('{\n  "channels": {\n    "chat": {\n      "verbosity": "low"\n    }\n  }\n}\n', encoding="utf-8")
        res = config_writer.set_value(path, "channels.chat.verbosity", "high")
        self.assertEqual(res, 0)
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(body["channels"]["chat"]["verbosity"], "high")

    def test_set_value_on_new_file(self) -> None:
        path = self.tmp / "new_config.json"
        res = config_writer.set_value(path, "channels.chat.verbosity", "medium")
        self.assertEqual(res, 0)
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(body["channels"]["chat"]["verbosity"], "medium")
        self.assertEqual(body["version"], 1)

    def test_set_value_absent_key_rewrites(self) -> None:
        path = self.tmp / "config.json"
        path.write_text('{"version": 1, "channels": {}}', encoding="utf-8")
        res = config_writer.set_value(path, "channels.chat.verbosity", "low")
        self.assertEqual(res, 0)
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(body["channels"]["chat"]["verbosity"], "low")


if __name__ == "__main__":
    unittest.main()
