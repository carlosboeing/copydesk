# CopyDesk

A prose gate for AI agents. It stops an agent writing badly, rather than telling you afterwards that it did.

```bash
npx copydesk check README.md
```

## The two positions nobody else occupies

Correction gets more expensive down this table. Refusing a write costs one retry. Scoring after the save costs a rewrite the model already committed to. Failing a build costs a round trip and a person's attention.

| When | What happens | Who is there |
|---|---|---|
| Before generation | Rules enter the model's context, so the text is never produced | CopyDesk |
| At write time | The write is refused and the model must revise | CopyDesk |
| After the file is saved | The file is scored and a fix is requested | [sloptrim](https://github.com/seyedehsanhadi/sloptrim) |
| In continuous integration | The build reports or fails | [Vale](https://vale.sh) |

The claim is structural rather than a claim about rule quality. Nobody else is standing in the first two rows.

## What that buys

**Prevention.** A 49-word précis enters the model's context every turn, shaping it before it writes. Vale has no producer to inject into, because human writers have no context window.

**Refusal.** The gate exits 2 and the write never reaches the file.

**Chat coverage.** Roughly half an agent's prose is chat that never reaches disk. The output style and the reminder cover it, and the [observational baseline](docs/evidence/observational-baseline.md) measures chat at 9.14 blocking violations per 1,000 words against 5.42 in documents. The worse half is the half a file linter cannot see.

**Retry escalation.** Two blocks, then a pass with a recorded warning. A hard block loop would deadlock a model that cannot satisfy a rule.

**Measurement.** Telemetry records words resent, blocks by origin, retry streaks and estimated token cost, so the cost of the gate is a number rather than a feeling.

## Where CopyDesk is behind

Worth knowing before you install it.

sloptrim reads more than 20 file formats including `.docx` and `.epub`, against Markdown alone here. Its 0-100 score also reads more clearly to a newcomer than a pass-or-fail list. On raw pattern count the two are close: sloptrim carries 71, CopyDesk 67 tokens across 7 pattern rules, with 8 more metric and structural rules.

Vale has eleven rule extension points, markup-aware parsing across four markup languages, official Microsoft and Google style guide rule sets, and editor integrations. CopyDesk has none of that.

CopyDesk will not win on breadth and does not try. It wins on when it intervenes.

## Install

```bash
npm install -g copydesk
```

Or from a checkout, which puts the command on your PATH and nothing else:

```bash
git clone https://github.com/carlosboeing/copydesk.git
cd copydesk
./install.sh
```

## Use

Lint files, or standard input:

```bash
copydesk check docs/guide.md docs/ROADMAP.md
printf '%s\n' 'A short sentence has enough words for this check.' | copydesk check -
```

Report on what the gate has been doing:

```bash
copydesk stats
copydesk stats --since 30d --json
copydesk report --out docs/telemetry-report.md
```

Check the installation without changing anything:

```bash
copydesk doctor
```

Exit codes: 0 clean, 1 findings, 2 a hook blocked the write, 64 usage.

## Install the gate on Claude Code

```bash
mkdir -p ~/.claude/hooks/copydesk/rules
cp hooks/gate.sh hooks/reminder.sh ~/.claude/hooks/copydesk/
cp lib/linter.py ~/.claude/hooks/copydesk/
cp rules/plain-english.json ~/.claude/hooks/copydesk/rules/
chmod +x ~/.claude/hooks/copydesk/gate.sh ~/.claude/hooks/copydesk/reminder.sh
```

Then register both hooks in `~/.claude/settings.json`:

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

The preset must travel with `linter.py`. The linter compiles its rules at import, so a copy without one reports `PresetNotFound` and stops checking.

## Configure

Two files, merged rather than chosen between: a user file at `$XDG_CONFIG_HOME/copydesk/config.json`, and the nearest `copydesk.config.json` walking up from the document.

```json
{
  "version": 1,
  "extends": "plain-english",
  "rules": {
    "sentence-length": { "severity": "warn", "max": 30 },
    "banned-word": { "add": ["synergy"], "remove": ["robust"] },
    "unglossed-term": { "vocabulary": { "add": ["React", "Postgres"] } },
    "nested-table": { "severity": "off" }
  }
}
```

Word lists take `add` and `remove` rather than replacement, so extending a preset never means restating it. Severity is `error`, `warn` or `off`.

**The gate fails open.** A broken config prints one line, records an event and lints with the built-in preset, because a hook that blocks on its own misconfiguration is worse than one that lets the write through.

Full detail in [docs/configuration.md](docs/configuration.md). Every rule and its parameters in [docs/rules.md](docs/rules.md).

## The rules

15 rules in three groups. Pattern rules are data you can edit. Metric rules are code with declared thresholds. Structural rules are toggles.

The one worth naming here is **`unglossed-term`**, which flags a capitalised term the first time it appears without a gloss nearby. Full detection needs named-entity recognition; the workable version is a vocabulary you maintain, in three layers so "React" needs no gloss anywhere while your own product name needs one outside its own repository.

## Evidence

- [Observational baseline](docs/evidence/observational-baseline.md) — 1,556,107 words of chat and 626,153 words of Markdown, measured with the scripts that ship in `eval/`.
- [Gate baseline](docs/evidence/baseline-results.md) — two continuous ten-turn sessions with the model, effort, approval mode and a pinned target commit all recorded.

Both are reproducible. Evidence nobody can check is not evidence.

## Status

`0.1.0`. Pre-1.0, in daily use by its author, with a public [roadmap](docs/ROADMAP.md). `1.0.0` is gated on one verifiable item: a TypeScript implementation passing the Python test suite through `bin/copydesk`.

## Licence

MIT. `lib/linter.py` is vendored and adapted from [`AminBlg/SimpleEnglish`](https://github.com/AminBlg/SimpleEnglish), also MIT; the full notice travels in the file and in [LICENSE](LICENSE).

The casing rule comes from CrossRev's [ADR 0010](https://github.com/carlosboeing/crossrev/blob/main/docs/adrs/0010-name-crossrev.md), adopted unchanged.
