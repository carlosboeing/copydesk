# Roadmap

Direction and sequencing. What shipped lives in the [changelog](../CHANGELOG.md).

## Shipped

**`0.1.0`** — extraction, the rules as data, the preset and config schema, `unglossed-term`, and the retry scope narrowed to the edited region.

## Next — `0.2.0`

**`copydesk learn`.** Scans committed prose and writes a diff of proposed vocabulary for review. Nothing it proposes applies until a person approves it.

Three risks make automatic learning dangerous, and all three shape the command.

The agent is the party being policed. If a model can write the vocabulary, it can silence its own violations by adding the term it just used.

Systems that learn from what passes drift toward silence. Every accepted write teaches acceptance, and the decay is invisible because it resembles improvement.

Project terms entering a global list means internal vocabulary escapes into public writing unchecked.

Spell-checkers settled this pattern decades ago: a base dictionary, a project dictionary committed to the repository, and adding a word as an explicit human action. `copydesk stats` will report vocabulary size over time, so growth outpacing the writing stays visible.

## Later — `1.0.0`

**A TypeScript port, YAML config, and a setup wizard.**

The release gate is one verifiable item: **the TypeScript implementation passes the Python test suite through `bin/copydesk`.** Not a feature list, and not a date.

`bin/copydesk` is the permanent entrypoint precisely so this is possible. Every installer, hook and document goes through it, so the implementation behind it can be replaced with no consumer seeing a difference.

YAML joins JSON rather than replacing it. Dropping a format is not backwards-compatible.

## Under consideration

**`copydesk install --git-hook`, a pre-commit hook for a consumer's own repository.** `install` is already reserved in the frozen command surface, so the name is waiting for it.

Today CopyDesk ships a `git-hooks/pre-commit` that runs *its own* test suite. That helps contributors here and nobody else. A consumer wanting their staged Markdown checked has to write the hook themselves, and the obvious one-liner is wrong: it lints whole files rather than staged content, so an unrelated pre-existing error blocks a commit that did not cause it.

The gate made the same mistake before `0.1.0` narrowed the retry scope, and the fix has the same shape. A hook should read `git diff --cached` and judge the added lines, not the file.

**It is a third pipeline position**, between the two CopyDesk occupies and the two it does not.

| When | What happens | Who is there |
|---|---|---|
| Before generation | Rules enter the model's context | CopyDesk |
| At write time | The write is refused | CopyDesk |
| **Before the commit** | **The commit is refused** | **nobody yet** |
| After the file is saved | The file is scored | sloptrim |
| In continuous integration | The build reports or fails | Vale |

It catches what the gate cannot see: prose written through Bash, prose from a harness with no blocking hook, and prose a human typed. Roughly half the reason the on-demand skill exists is that same gap.

Open questions that decide the shape.

1. **Does it lint staged content or the working tree?** Staged is correct and harder. `git show :file` reads the staged blob, and the added-line ranges come from `git diff --cached --unified=0`.
2. **Does it block, or warn?** A hook people bypass reflexively is worse than none. `--no-verify` must stay obvious and documented.
3. **Does `install` also cover the harness hooks?** The Claude Code gate is currently five manual `cp` commands in the README. One subcommand covering both is tidier, but it widens a reserved name into a larger promise.

Prior art worth reading before building: the retry-scope narrowing in `0.1.0`, which measured a benign edit's block rate falling from 72 per cent to 2 per cent once the decision moved from the document to the edited region.

**`copydesk import vale <style>`.** Vale's `existence` and `substitution` checks are token lists carrying a message and a severity, and CopyDesk's pattern format deliberately matches that shape. An importer would be a translation rather than a redesign, and would hand CopyDesk the Microsoft and Google style guides plus the write-good, alex and proselint ports as presets. Vale is MIT licensed, so its ecosystem is usable.

**Harness adapters beyond Claude Code.** The core takes text and a path and returns findings; everything harness-specific is payload translation at the edge. Claude Code is verified against live calls. Kimi Code, Cursor and Grok Build TUI each need a live transcript check before their adapter is claimed as working. Codex and Antigravity have no confirmed blocking hook and are covered by the on-demand skill.

**A `fix` command.** Rules that can be fixed mechanically could be, rather than reported. It is the second half of the issue that produced the retry-scope narrowing.

**A neutral preset.** `plain-english` is one opinionated rule set. A less strict default probably wants to exist, and it has no name yet.

## Declined for now

**`copydesk export vale`.** It assumes teams want an agent-writing style guide running in continuous integration, and no evidence supports that yet. Recording it as planned work would create an expectation the project may never meet.

**Embedding Vale as a runtime dependency.** The macOS arm64 release is a 10.5 MB compressed download against a 64 KB Python file. The gate runs at a 17 ms median on every Write and Edit and must fail open, so a Go binary spawn on that path is a latency and install-friction cost. Four current rules are structural analyses Vale's extension points do not express, so running two engines would still not cover one rule set.
