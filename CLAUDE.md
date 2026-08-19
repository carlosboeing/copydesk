# CopyDesk — instructions for AI agents

Auto-loaded on every session. `AGENTS.md` symlinks here for harnesses that expect that filename.

CopyDesk is a prose gate for AI agents. It occupies two pipeline positions no other tool stands in: rules enter the model's context before generation, and a write is refused at write time so the model must revise. The rule engine is harness-neutral; per-harness adapters translate payloads at the edge.

## How the name is written

Adopted unchanged from CrossRev's ADR 0010.

**`CopyDesk` is the default in every human-readable reference.** Printed command output, the prose inside help text, documentation, skill text and diagram labels all take `CopyDesk`.

**Lowercase `copydesk` is an allowlist, not a split down the middle.** The complete list: the command and its arguments, the npm package name, config filenames, installed paths, environment-variable prefixes in their lowercase forms, and any string a script matches literally.

The trap that catches people: in help text, `copydesk check README.md` stays lowercase and the sentence above it does not.

`plain-english` is a preset name, not the tool. Every string naming that preset keeps its own spelling, including the output style's `name: Plain English` frontmatter, the `plain-english-rules` block markers, and `rules/plain-english.json`.

## Two repositories, one working tree

A private working-memory sidecar sits beside this repository: `carlosboeing/copydesk-workbench`, cloned as an independent git repository at `.workbench/` and git-ignored here. Penmark and CrossRev both use the same arrangement.

**Never cross-commit.** Plain `git …` targets this public repository. `git -C .workbench …` targets the private workbench. No command legitimately stages both.

Working memory — brainstorms, designs, plans, reviews, notes, and the captured evaluation transcripts — lives in the workbench. Code, public documentation, ADRs, ROADMAP and CHANGELOG live here.

A pre-commit hook at `git-hooks/private-workbench-guard/pre-commit` refuses a commit that stages the workbench as a gitlink. `git add -f .workbench` records a submodule reference, publishing the private repository's name and commit SHA even though clones get none of its content.

## The compatibility contract

Six items are frozen from the first public release. A language rewrite must not move them. Everything else is internal and free to change.

1. **A `version` field in the config file.** Without it no schema migration is possible.
2. **Rule identifiers.** `sentence-length`, `banned-word`, `orphan-pointer` and the rest. An alias map absorbs future renames.
3. **A three-value severity vocabulary:** `error` blocks, `warn` reports, `off` disables. A fourth value later is a migration.
4. **Additive list semantics.** Word lists take `add` and `remove`, never replacement. Replacement makes extending a preset require restating it.
5. **`extends` accepting a string or an array**, later entries winning, with an explicit `rules` block overriding everything.
6. **The command-line surface:** commands, flags, environment variables and exit codes.

The telemetry event schema is versioned separately and joins this list unchanged.

**Deliberately free:** which rules are compiled and which are data, the implementation language, the internal rule representation, how presets are stored on disk, and whether more rule extension points arrive later.

`bin/copydesk` is the permanent entrypoint. Every installer, hook and document goes through it, so a later implementation replaces what sits behind it with no consumer seeing a difference.

## Distribution

Published to npm as **`copydesk`**. The name was free and npm accepted it, so the `copydesk-ai` fallback was never needed.

The fallback existed because npm refused `crossrev` for a sibling project, and that check runs only at publish time with no advance query and no appeal. The `-ai` suffix is a collision workaround for that one name rather than a house convention.

Versioning follows the house pattern: a root `VERSION` file, `vX.Y.Z` tags, a floating `v0`, and Keep a Changelog format.

## Working principles

- **The test suite is the safety net and it stays green.** `python3 -m unittest discover tests/`. The pre-commit hook runs it with no path guard, because an earlier guard exited 0 in silence once its path moved.
- **Every scan states a control.** A scan returning nothing proves nothing until a pattern known to be present returns a hit through the same command form.
- **The rule data travels with the linter.** `lib/linter.py` compiles `rules/plain-english.json` at import. An installed hook copy needs the preset beside it, or `COPYDESK_RULES` pointing at one. Without it the module raises `PresetNotFound` and the gate exits non-blocking.
- **Carriers are generated, never hand-edited.** `scripts/generate-carriers.py` renders `output-styles/plain-english.md` and the reminder precis from the preset. Continuous integration runs `--check`.
- **The gate fails open.** A hook that blocks on its own misconfiguration is worse than one that lets the write through. It prints the error and records a telemetry event.
- **Conventional Commits.** `<type>(<scope>): <description>`, imperative mood, subject at most 72 characters. Never write a bare `#N` unless it references a real GitHub issue.
- **No emojis in files** unless asked.
