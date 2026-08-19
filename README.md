# CopyDesk

CopyDesk is the shared Markdown linter for the writing-style experiment. It supplies one rule set to the command line, the future Claude Code gate, and the later measurement scripts.

## Components

| Path | Purpose |
|---|---|
| `bin/copydesk` | CLI for Markdown paths, stdin, stats, and reports. |
| `install.sh` | Checkout-only CLI installer; creates a user-local symlink and does not install the skill or hook. |
| `lib/linter.py` | Vendored linter, shared exclusions, checks, telemetry writer, and summariser. |
| `hooks/gate.sh` | PreToolUse wrapper for Markdown Write and Edit calls. |
| `hooks/reminder.sh` | UserPromptSubmit per-turn style reminder hook (49-word précis) with turn telemetry tick. |
| `skills/copydesk/SKILL.md` | On-demand check for work written through Bash or outside Claude Code. |
| `tests/` | Bad, good, and excluded Markdown fixtures with automated test suite. |

## Use from this bundle

Run the command against one or more files, or pass `-` for standard input:

```bash
bin/copydesk README.md docs/ROADMAP.md
printf '%s\n' 'A short sentence has enough words for this check.' | bin/copydesk -
```

It prints each result as `line:check:excerpt`. Errors return exit code 1. Warnings remain visible but do not fail the command.

## Telemetry dashboard and reports

Inspect live telemetry metrics across the gate and CLI surfaces:

```bash
# Print terminal dashboard for all recorded events
copydesk stats

# Filter by window (e.g. 30 days, 14 days, or YYYY-MM-DD)
copydesk stats --since 30d

# Machine-readable JSON summary
copydesk stats --json

# Generate Markdown report in eval/telemetry/ (or custom destination)
copydesk report
copydesk report --out docs/telemetry-report.md
```

### Telemetry environment variables

- `COPYDESK_LOG=0`: Disables event writing entirely.
- `COPYDESK_LOG_FLAGGED_TEXT=0`: Logs rule names and line numbers while omitting `flagged_text` snippets for privacy.
- `COPYDESK_STATE_DIR`: Overrides the default state directory (`$XDG_STATE_HOME/copydesk/`, falling back to `~/.local/state/copydesk/`).

## Install the CLI from a checkout

The on-demand skill invokes `copydesk` from `PATH`. A checkout owner can expose this bundle's command once:

```bash
install.sh
```

That creates `~/.local/bin/copydesk` as a symlink to this checkout. Use `--bin-dir` for a different local bin directory:

```bash
install.sh --bin-dir /path/to/bin
```

The installer neither downloads code nor installs the skill or Claude Code hook. Task 1 builds and tests it only; Task 5 may run it after the experiment's verdict. Ensure the chosen bin directory is on `PATH` before using the skill.

## Claude Code hooks

Copy the hook files into one private directory so the gate wrapper can find the Python module without a repository-relative path:

```bash
mkdir -p ~/.claude/hooks/copydesk
cp hooks/gate.sh ~/.claude/hooks/copydesk/gate.sh
cp hooks/reminder.sh ~/.claude/hooks/copydesk/reminder.sh
cp lib/linter.py ~/.claude/hooks/copydesk/linter.py
chmod +x ~/.claude/hooks/copydesk/gate.sh ~/.claude/hooks/copydesk/reminder.sh
```

Then add this fragment under `hooks` in `~/.claude/settings.json`:

```json
{
  "PreToolUse": [
    {
      "matcher": "Write|Edit",
      "hooks": [
        {
          "type": "command",
          "command": "~/.claude/hooks/copydesk/gate.sh"
        }
      ]
    }
  ],
  "UserPromptSubmit": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "~/.claude/hooks/copydesk/reminder.sh"
        }
      ]
    }
  ]
}
```

Task 1 only builds these files. It does not run the CLI installer, copy hook files into `~/.claude/`, or install the skill. Those changes wait for the Task 5 verdict.

## Failure behaviour

The wrapper exits quickly for tools other than Markdown Write or Edit. Missing Python, malformed JSON, missing fields, unreadable files, and mismatched Edit text pass through unchanged.

For a valid Write, it lints `content`. For a valid Edit, it reconstructs the proposed file in memory before linting. A blocking check exits 2 and returns `line:check:excerpt` messages. AI-tell failures also point to `/humanizer`.

Retry state lives in `$XDG_STATE_HOME/copydesk/<session_id>.json`, falling back to `~/.local/state/copydesk/<session_id>.json`. The state write uses a temporary file and atomic rename. The hook blocks the first two failed attempts for each session and file. The third passes with a warning that records the relevant content hash. Entries older than 24 hours are removed during later calls.

## Verified input shape

The field names came from the live JSONL transcript corpus on 2026-08-17, not from a documentation summary:

| Tool | Fields | Calls |
|---|---|---:|
| `Edit` | `file_path`, `new_string`, `old_string`, `replace_all` | 5,161 |
| `Write` | `content`, `file_path` | 739 |
| `Write` | `subagent_name`, `subagent_parameters`, `subagent_type` | 1 |

The final row has no file path. The wrapper fails open for it and any other payload without the expected fields.

## Attribution

`lib/linter.py` vendors and adapts `evals/ste_lint.py` from [AminBlg/SimpleEnglish](https://github.com/AminBlg/SimpleEnglish), commit `59bf6702197a5aadc96d197ea17f290d8d50dcd3`, under MIT. The file header records the full notice and the deliberate removal of upstream bans on contractions, modals, and semicolons.

