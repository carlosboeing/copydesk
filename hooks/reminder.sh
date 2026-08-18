#!/usr/bin/env bash
# Plain English per-turn chat reminder for Claude Code UserPromptSubmit hook.
# Hand-written précis (47 words) reinforcing style against long-session decay.

set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/linter.py" ]]; then
  LINTER="$HOOK_DIR/linter.py"             # installed ~/.claude/hooks/plain-english/
elif [[ -f "$HOOK_DIR/../lib/linter.py" ]]; then
  LINTER="$HOOK_DIR/../lib/linter.py"      # source bundle
else
  LINTER=""
fi

if [[ -n "$LINTER" ]] && command -v python3 >/dev/null 2>&1; then
  python3 "$LINTER" --turn >/dev/null 2>&1 || true
fi

cat << 'EOF'
Answer in one bold line first. Numbered sections split by `---`; in chat, no `##` headers — they render as plain bold. Sentences under 25 words, each self-contained. Gloss every link, SHA and line number inline. End with a numbered decisions list; nothing actionable lives only in the body.
EOF
