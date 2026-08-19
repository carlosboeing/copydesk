#!/usr/bin/env bash
# PreToolUse wrapper. The Python module owns parsing, reconstruction, linting,
# and retry state so every path uses the same exclusion implementation.

set -u

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HOOK_DIR/linter.py" ]]; then
  LINTER="$HOOK_DIR/linter.py"             # installed ~/.claude/hooks/copydesk/
elif [[ -f "$HOOK_DIR/../lib/linter.py" ]]; then
  LINTER="$HOOK_DIR/../lib/linter.py"      # source bundle
else
  exit 0
fi

exec python3 "$LINTER" --hook
