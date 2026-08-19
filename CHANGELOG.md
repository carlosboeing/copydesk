# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
