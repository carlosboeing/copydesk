# CopyDesk

[![npm](https://img.shields.io/npm/v/copydesk.svg)](https://www.npmjs.com/package/copydesk)
[![test](https://github.com/carlosboeing/copydesk/actions/workflows/test.yml/badge.svg)](https://github.com/carlosboeing/copydesk/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![status: pre-1.0](https://img.shields.io/badge/status-pre--1.0-orange.svg)](docs/ROADMAP.md)

**A prose gate and style system for AI coding agents.**

It prevents violations before generation, and refuses the write when a violation slips through.

Every other prose tool reads your text after it is saved. CopyDesk works in two earlier moments: it compiles instructions into the model context before generation, and it intercepts file edits to refuse violations at write time.

| When | What happens | Who is there |
|---|---|---|
| Before generation | Instructions enter the model context, so bad prose is never produced | **CopyDesk** |
| At write time | The write is refused and the model revises | **CopyDesk** |
| After the file is saved | The file is scored and a fix requested | sloptrim |
| In continuous integration | The build reports or fails | Vale |

---

## What it looks like

### 1. Prevention (before generation)

CopyDesk generates tuned output styles and instruction blocks for your agents. The model knows your style, verbosity, and constraints before writing a single token.

```
Answer first, then support it.
Give the answer and one line of support.
Cut any sentence that does not change what the reader knows or does.
```

### 2. Remediation (at write time)

When an agent ignores instructions during a file write, the gate refuses the operation. The agent revises immediately.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/gate-demo-dark.png">
  <img alt="A terminal showing an agent edit refused by the CopyDesk gate." src="docs/assets/gate-demo-light.png" width="100%">
</picture>

The gate blocks on new findings in the edited region, not on pre-existing errors in the file. It tells the model not to touch surrounding code or text.

You can also run CopyDesk as a standalone CLI linter:

```console
$ copydesk check release-notes.md
release-notes.md:
3:announcing-opener:Great question — let me walk you through it. This release delivers a solid and
3:sentence-length:This release delivers a solid and significant overhaul of the export pipeline…
8:idiom:That said, we should circle back on the remaining items. As noted above, the
8:orphan-pointer:That said, we should circle back on the remaining items. As noted above, the
9:soft-offer:former approach is deprecated. Happy to walk through any of this if you'd like.
```

## Why this exists

AI coding agents produce four channels of prose: terminal chat, Markdown documentation, commit messages, and code review comments.

Most unguided agent writing shares four flaws. Sentences stretch past forty words. Openers announce what is coming instead of answering. Chat repeats answered decisions turn after turn.

Post-hoc linters catch these issues in CI or after saving. That costs a full retry round trip or reviewer attention.

Measured across [1.5 million words of real transcripts](docs/evidence/observational-baseline.md), terminal chat carries 9.14 blocking violations per 1,000 words against 5.42 in documents. Chat never becomes a saved file, so a post-hoc file linter cannot inspect it. Prevention through compiled context instructions is the only way to reach chat.

## Contents

- [Install](#install)
- [Quickstart: Setup Wizard](#quickstart-setup-wizard)
- [Channels](#channels)
- [Styles](#styles)
- [Usage](#usage)
- [Configuration](#configuration)
- [Rules](#rules)
- [Setting up the gate by hand](#setting-up-the-gate-by-hand)
- [How it compares](#how-it-compares)
- [Evidence](#evidence)
- [Project status](#project-status)
- [Contributing](#contributing)
- [Security](#security)
- [Licence and credits](#licence-and-credits)

## Install

```bash
npm install -g copydesk
```

Or run directly without global installation:

```bash
npx copydesk check README.md
```

Or clone from source:

```bash
git clone https://github.com/carlosboeing/copydesk.git
cd copydesk
./install.sh
```

CopyDesk requires Python 3.9 or later and has zero third-party dependencies.

## Quickstart: Setup Wizard

Run the interactive wizard to configure styles, channels, and hooks:

```bash
copydesk setup
```

The wizard guides you through three choices:
1. Preset style and verbosity for each channel.
2. Connected harnesses and tools.
3. Configuration review and proof run.

You can also run unattended with defaults:

```bash
copydesk setup --defaults --yes
```

To check your installation and effective rules:

```bash
copydesk doctor
```

To remove all CopyDesk hooks and configurations:

```bash
copydesk uninstall
```

## Channels

CopyDesk divides agent writing into four channels:

| Channel | Medium | Gate mechanism | Default style |
|---|---|---|---|
| `chat` | Terminal conversational replies | Prevention only (context instructions) | `plain` (low verbosity) |
| `documents` | Markdown files on disk | `PreToolUse` write/edit hook | `plain` (high verbosity) |
| `commits` | Git commit messages | `commit-msg` git hook | `engineer` (low verbosity) |
| `reviews` | PR and code review markdown | Configured `match` file patterns | `plain` (medium verbosity) |

Read more in [docs/channels.md](docs/channels.md).

## Styles

CopyDesk ships four base styles:

| Style | Purpose | Best suited for |
|---|---|---|
| `plain` | Answer first, concise supporting facts | Day-to-day coding, technical documentation |
| `engineer` | Terse procedures, tables, minimal prose | API references, runbooks, schemas |
| `editorial` | Narrative explanations, flowing paragraphs | Thought leadership, blog posts |
| `general` | Plain terms with every specialized word glossed | Onboarding guides, non-technical docs |

Read more in [docs/styles.md](docs/styles.md).

## Usage

Check files or standard input:

```bash
copydesk check docs/guide.md CHANGELOG.md
cat draft.md | copydesk check -
copydesk check --commit-msg .git/COMMIT_EDITMSG
```

Inspect rules and resolution:

```bash
copydesk doctor                  # explain active settings and health
copydesk doctor docs/guide.md    # explain rules for a specific file
copydesk doctor --rules          # list all rules and guidance parameters
```

Switch chat verbosity quickly:

```bash
copydesk set channels.chat.verbosity=medium
```

View local gate telemetry:

```bash
copydesk stats                    # activity summary
copydesk stats --since 30d --json # JSON format
copydesk report --out report.md   # detailed markdown report
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Clean, or warnings only |
| `1` | Blocking prose findings |
| `2` | Hook blocked a write |
| `64` | Usage error |
| `70` | Internal error (fails open for git commits) |

## Configuration

Configuration is optional. By default, the `plain` preset applies.

CopyDesk merges configuration across three locations:

| Layer | Path | Purpose |
|---|---|---|
| User | `~/.config/copydesk/config.json` | Personal defaults |
| Project | `copydesk.config.json` | Repository standards |
| Local | `copydesk.local.json` | Local overrides |

JSON with comments (JSONC) is supported.

```json
{
  "$schema": "https://raw.githubusercontent.com/carlosboeing/copydesk/v0/copydesk.schema.json",
  "version": 1,
  "channels": {
    "chat": { "style": "plain", "verbosity": "low" },
    "documents": { "style": "engineer" }
  },
  "paths": {
    "ignore": [".workbench/**", "drafts/**"],
    "warn": ["CHANGELOG.md"]
  },
  "rules": {
    "sentence-length": { "severity": "warn", "max": 30 },
    "banned-word": { "add": ["synergy"], "remove": ["solid"] },
    "unglossed-term": { "add": ["React", "Postgres"] }
  }
}
```

Full details are in [docs/configuration.md](docs/configuration.md).

## Rules

15 rules across three groups:

| Group | Rules |
|---|---|
| Pattern | `banned-word`, `idiom`, `soft-offer`, `announcing-opener`, `contrast-construction`, `orphan-pointer`, `verb-jargon` |
| Metric | `sentence-length`, `paragraph-length`, `avg-sentence-length`, `long-sentence-rate`, `sentence-variation`, `list-dominated`, `unglossed-term` |
| Structural | `nested-table` |

Every rule and parameter is documented in [docs/rules.md](docs/rules.md).

## Setting up the gate by hand

`copydesk setup` handles installation automatically. If you prefer manual setup for Claude Code:

1. Copy hooks and rules:
   ```bash
   mkdir -p ~/.claude/hooks/copydesk/rules ~/.claude/output-styles
   cp hooks/gate.sh hooks/reminder.sh lib/linter.py ~/.claude/hooks/copydesk/
   cp rules/plain.json ~/.claude/hooks/copydesk/rules/
   chmod +x ~/.claude/hooks/copydesk/gate.sh ~/.claude/hooks/copydesk/reminder.sh
   ```

2. Register in `~/.claude/settings.json`:
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Write|Edit",
           "hooks": [{ "type": "command", "command": "~/.claude/hooks/copydesk/gate.sh" }]
         }
       ],
       "UserPromptSubmit": [
         {
           "hooks": [{ "type": "command", "command": "~/.claude/hooks/copydesk/reminder.sh" }]
         }
       ]
     }
   }
   ```

3. Run `copydesk doctor` to verify registration.

## How it compares

| When | What happens | Who is there |
|---|---|---|
| Before generation | Instructions enter the model context | **CopyDesk** |
| At write time | The write is refused | **CopyDesk** |
| After the file is saved | The file is scored | sloptrim |
| In continuous integration | The build reports or fails | Vale |

Where CopyDesk differs:

| Tool | Differences |
|---|---|
| **Vale** | Markup-aware parsing, official style packages, and editor integrations. |
| **sloptrim** | Supports 20+ document formats and calculates holistic scores. |
| **CopyDesk** | Real-time latency with zero runtime dependencies and fail-open guarantees. |

## Evidence

- **[Observational baseline](docs/evidence/observational-baseline.md)**: Analysis across 1,556,107 words of chat and 626,153 words of Markdown.
- **[Gate baseline](docs/evidence/baseline-results.md)**: Multi-turn session evaluations tracking rule enforcement and instruction decay over time.

## Project status

**`0.2.0`, pre-1.0.** The CLI commands, config schema, rule identifiers, and channel definitions are stable.

See [docs/ROADMAP.md](docs/ROADMAP.md) for future plans and [CHANGELOG.md](CHANGELOG.md) for release history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for test execution and contribution guidelines.

## Security

CopyDesk runs locally and makes no outbound network connections. Document contents never leave your machine. See [SECURITY.md](SECURITY.md).

## Licence and credits

MIT. See [LICENSE](LICENSE).

`lib/linter.py` is adapted from [`AminBlg/SimpleEnglish`](https://github.com/AminBlg/SimpleEnglish) (MIT).
