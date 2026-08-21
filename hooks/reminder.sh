#!/usr/bin/env bash
# CopyDesk per-turn chat reminder for Claude Code UserPromptSubmit hook.
# Hand-written précis (49 words) reinforcing style against long-session decay.
# linter.REMINDER_WORD_COUNT must match; tests/test_telemetry.py checks it.

set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/linter.py" ]]; then
  LINTER="$HOOK_DIR/linter.py"             # installed ~/.claude/hooks/copydesk/
elif [[ -f "$HOOK_DIR/../lib/linter.py" ]]; then
  LINTER="$HOOK_DIR/../lib/linter.py"      # source bundle
else
  LINTER=""
fi

if [[ -n "$LINTER" ]] && command -v python3 >/dev/null 2>&1; then
  python3 "$LINTER" --turn >/dev/null 2>&1 || true
  if python3 "$LINTER" --reminder 2>/dev/null; then
    exit 0
  fi
fi

cat << 'EOF'
Answer in one bold line first. Numbered sections split by `---`; in chat, no `##` headers — they render as plain bold. Sentences under 25 words, each self-contained. Gloss every link, SHA and line number inline. End with a numbered decisions list; nothing actionable lives only in the body.
EOF
