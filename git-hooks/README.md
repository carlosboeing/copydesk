# git-hooks/

Git pre-commit hook guarding the CopyDesk bundle and canonical rules sync.

## What it guards

Runs the CopyDesk test suite (`tests/`):
1. `test_checks.py`: verifies that all deterministic linter patterns correctly trigger on bad fixtures and ignore good or excluded content.
2. `test_rules_sync.py`: verifies that the canonical rules block in `output-styles/plain-english.md` and `~/.claude/CLAUDE.md` remain byte-identical, and that every quoted phrase is represented in the linter's pattern inventory.

## What it deliberately does not do

- Does not lint arbitrary repository Markdown files on commit: historical or draft documents may contain stylistic variation, and wholesale blocking on pre-existing files impedes work. The on-demand `/copydesk` skill and Claude Code `PreToolUse` gate handle interactive and authored files instead.
- Does not modify any staged files automatically.

## Install

Install the repository shim and configure git:

```bash
mkdir -p scripts/githooks
cat << 'EOF' > scripts/githooks/pre-commit
#!/usr/bin/env bash
exec git-hooks/pre-commit
EOF
chmod +x scripts/githooks/pre-commit
git config core.hooksPath scripts/githooks
```
