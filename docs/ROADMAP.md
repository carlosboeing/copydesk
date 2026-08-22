# Roadmap

Direction and sequencing. What shipped lives in the [changelog](../CHANGELOG.md).

## Shipped

- **`0.1.0`** — extraction, the rules as data, the preset and config schema, `unglossed-term`, and the retry scope narrowed to the edited region.
- **`0.2.0`** — prevention system: four channels, the styles shelf, guidance deliverables, the setup wizard, uninstaller, commit-msg hook, and doctor.
- **`0.3.0`** — the `hook` subcommand: the commit-msg hook managed across repositories, with a state-directory registry, chaining into foreign hooks, and uninstall covering every recorded repository.

## Next — `1.0.0`

**A TypeScript port passing the Python test suite.**

The release gate is one verifiable item: **the TypeScript implementation passes the Python test suite through `bin/copydesk`.** Not a feature list, and not a date.

`bin/copydesk` is the permanent entrypoint precisely so this is possible. Every installer, hook and document goes through it, so the implementation behind it can be replaced with no consumer seeing a difference.

YAML configuration support was evaluated and dropped: support for comments in JSON (JSONC) removed the primary motive, and the Python standard library does not bundle a YAML parser.

## Under consideration

- **`copydesk learn`**: Scans committed prose and writes a diff of proposed vocabulary for review. Nothing it proposes applies until a person approves it.
- **Retry-scope attributing pre-existing sentences ([#8](https://github.com/carlosboeing/copydesk/issues/8))**: Narrowing the remediation scope on edits to avoid false positive blocks on pre-existing text surrounding an edit.
- **`copydesk import vale <style>`**: Vale's `existence` and `substitution` checks are token lists carrying a message and a severity, matching CopyDesk's pattern format.
- **Harness adapters beyond Claude Code**: Native adapters for Cursor, Kimi Code, Grok Build TUI, Codex, and Antigravity.
- **A `fix` command**: Mechanically fix straightforward style findings rather than reporting them for manual revision.

## Declined for now

- **`copydesk export vale`**: It assumes teams want an agent-writing style guide running in continuous integration, and no evidence supports that yet.
- **Embedding Vale as a runtime dependency**: A heavy binary spawn adds latency to real-time gate evaluation, and Vale cannot evaluate Markdown structural and metric rules.
