#!/usr/bin/env bash
# Install the checkout's CLI on PATH. This does not install the skill or hook.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$HERE/bin/copydesk"
BIN_DIR="${COPYDESK_BIN_DIR:-$HOME/.local/bin}"
ASSUME_YES=0

while (( $# )); do
  case "$1" in
    --bin-dir) BIN_DIR="${2:?--bin-dir needs a path}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --help|-h)
      echo "usage: install.sh [--bin-dir <dir>] [--yes]"
      exit 0 ;;
    *)
      echo "error  unknown option: $1" >&2
      exit 1 ;;
  esac
done

if [[ ! -x "$CLI" || ! -f "$HERE/lib/linter.py" ]]; then
  echo "error  install.sh must run from a complete CopyDesk bundle." >&2
  exit 1
fi

TARGET="$BIN_DIR/copydesk"
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ -d "$TARGET" && ! -L "$TARGET" ]]; then
    echo "error  $TARGET is a directory." >&2
    exit 1
  fi

  EXISTING="$(readlink "$TARGET" 2>/dev/null || printf '%s' "$TARGET")"
  if [[ "$ASSUME_YES" != "1" && ! "$TARGET" -ef "$CLI" ]]; then
    echo "error  $TARGET already exists and points somewhere else: $EXISTING" >&2
    echo "       Re-run with --yes to replace it." >&2
    exit 1
  fi
fi

mkdir -p "$BIN_DIR"
ln -sf "$CLI" "$TARGET"

if [[ ! -x "$TARGET" ]]; then
  echo "error  $TARGET was created but does not resolve to an executable." >&2
  exit 1
fi

echo "installed $TARGET -> $CLI"
