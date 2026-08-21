# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2]

### Fixed

- **Arrow keys cancelled `copydesk setup` instead of navigating it.** The key reader took the first byte through a text wrapper, which pulled the rest of the escape sequence into its own buffer. The `select` that asks whether more bytes are waiting then looked at an empty file descriptor and concluded that Escape had been pressed alone. Bytes now come off the descriptor, where `select` can see them.
- **Any unrecognised escape sequence cancelled the wizard.** Home, End, Page Up, Page Down, Shift+Tab, the function keys, a bracketed paste and a mouse click all arrive as escape sequences, and all of them were read as a bare Escape. The reader now consumes each sequence to its end and ignores the ones that do not navigate.

### Changed

- **The key hint bar uses arrow glyphs**: `↑/↓ to navigate · Space to toggle · Enter to confirm · Esc to go back`. A terminal that cannot encode them, such as one running under `LANG=C`, gets `up/down to navigate - Space to toggle - ...` instead, because writing a glyph such a terminal cannot print raises rather than degrading.

## [0.2.1]

### Changed

- **Marked-block splicing has one implementation.** `apply.splice_marked_block(existing, block)` returns the new file text and touches no disk, so setup computes every region before the plan writes anything. The setup wizard calls it instead of repeating the splice and reaching into a private regular expression.

### Removed

- **`apply.write_marked_block`.** No production code called it. It also caught `OSError` on read and continued with empty text, so a file that existed but could not be read would have been replaced by CopyDesk's region alone. Setup lets that read raise, which stops the run before any write. The seven tests that covered it moved onto the surviving path.

## [0.2.0]

### Added

- **Channel-aware prevention system.** Four distinct channels (`chat`, `documents`, `commits`, `reviews`) configure styles, verbosity levels, and guidance deliverables per medium.
- **Style shelf and behavioral floor.** Four base styles (`plain`, `engineer`, `editorial`, `general`) with a shared non-negotiable floor (answer first, closing block reserved, say once).
- **Guidance deliverables.** Ten configurable structural elements: `recommendations`, `direction`, `progress`, `pushback`, `alternatives`, `assumptions`, `estimates`, `sources`, `summary`, and `verification`.
- **Interactive setup wizard.** `copydesk setup` (alias `copydesk init`) provides guided setup across styles, channels, and harnesses with `--defaults`, `--yes`, `--dry-run`, and `--repair` flags.
- **Uninstaller.** `copydesk uninstall` cleanly removes CopyDesk-owned hooks and styles, with an optional `--purge` flag to remove user configuration.
- **Commit-message gate.** `git-hooks/commit-msg` and `copydesk check --commit-msg` enforce subject length, imperative subject openers, and prose rules at commit time. `copydesk setup` installs the hook when the current directory is a git repository.
- **JSON Schema.** `copydesk.schema.json` provides editor auto-completion and validation for configuration files.
- **Path routing engine.** The `paths` configuration block (`ignore`, `warn`, `block`) controls file matching and action overrides.
- **Three-location discovery.** Configuration merges across user (`~/.config/copydesk/config.json`), project (`copydesk.config.json`), and local (`copydesk.local.json`) with JSONC comment support.
- **Expanded doctor.** `copydesk doctor <file>` explains effective rules and provenance; `copydesk doctor --rules` lists rules and guidance deliverables; bare `copydesk doctor` performs drift checks.

### Changed

- **Preset identifier.** `plain-english` is renamed to `plain`, with `plain-english` retained indefinitely as an alias.
- **Configuration parameter names.** Threshold parameters standardized to canonical camelCase (`hardMax`, `maxSentences`, `maxRate`, `minStdev`, `exemptionRatio`), with snake_case aliases preserved.
- **Instruction generation.** Replaced `generate-carriers.py` with `generate-instructions.py` compiling output styles and prompt reminders.

### Fixed

- **Threshold propagation defect.** Configured `sentence-length.max` threshold reaches linter evaluation instead of being lost during resolution.

## [0.1.0]

First public release. CopyDesk was extracted from a personal resources repository, where it enforced one person's writing rules, and became a rule engine that ships those rules as one preset among several.

### Added

- **A rule engine with the rules as data.** `rules/plain-english.json` carries 67 pattern tokens across 8 blocks, the metric and structural thresholds, and the prose the carriers are rendered from. Tokens are regular expressions matching Vale's `existence` check, so a plain word needs no regex knowledge.
- **A config cascade.** A user file at `$XDG_CONFIG_HOME/copydesk/config.json` and the nearest `copydesk.config.json` merge over the built-in preset. Word lists take `add` and `remove` rather than replacement, and `extends` accepts a string or an array.
- **`unglossed-term`.** Flags a capitalised term on its first use when it is not sentence-initial, is absent from the merged vocabulary, and carries no gloss in its sentence. Ships at `warn` with 32 universal terms, each carrying a recorded reason.
- **`check` and `doctor` subcommands**, alongside the existing `stats` and `report`.
- **Evidence pages** under `docs/evidence/`, with the measurement scripts that produced them.

### Changed

- **The gate blocks on the edited region rather than the whole document.** A benign edit blocked 72 per cent of a 167-file Markdown corpus before this change and 2 per cent after. The gate prints only the findings that caused the block, plus a count of pre-existing errors that need no change.
- **The state directory left the Claude Code path.** It is `$XDG_STATE_HOME/copydesk/`, falling back to `~/.local/state/copydesk/`. `COPYDESK_STATE_DIR` still overrides it.
- **Environment variables took the `COPYDESK_` prefix**: `COPYDESK_LOG`, `COPYDESK_LOG_FLAGGED_TEXT`, `COPYDESK_STATE_DIR`, and the new `COPYDESK_RULES`.
- **The command is `copydesk`**, and the on-demand skill moved with it.
- **The output style and the reminder précis are generated** from the preset by `scripts/generate-carriers.py`. They stopped being hand-maintained copies.

### Fixed

- The pre-commit hook guarded on a bundle path and exited 0 in silence once that path moved. The guard is gone.
