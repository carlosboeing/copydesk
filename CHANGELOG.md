# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Every third `copydesk setup` reported a proof failure that was not one.** The proof sends one known-bad sample under one fixed session id, and the gate lets identical content through on the third consecutive submission — the retry escape valve working as designed. The proof's retry state outlived each run, so the third consecutive setup tripped the valve against a healthy install and ended with `Setup complete, but proof run failed`. The proof now deletes its own session state before it runs, so every proof starts with no history behind it; the session file also stops accumulating one entry per sample path across setups. The gate itself is unchanged.
- **The turn reminder's fallback had no unit-level pin to the preset.** The 49-word reminder exists twice: the preset's `reminder` field, and a fallback heredoc in the reminder hook for when the linter cannot be reached. `scripts/generate-instructions.py --check` already fails if those two diverge. A test now asserts the strings are equal, so a mismatch names the reminder rather than reporting that the hook file differs — this reminder is the only CopyDesk text that enters the model context on every turn.
- **The test suite wrote into the developer's own state directory.** Tests redirected `XDG_CONFIG_HOME` but not `XDG_STATE_HOME`, so every gate subprocess resolved the default state path: temporary-home paths piled up in the real proof session file, and fixture paths landed as `config_error` events in the telemetry a user reads with `copydesk stats`. Every redirect now sets both variables and drops `COPYDESK_STATE_DIR` so that override cannot keep pointing at the real directory, and one test asserts a full setup run leaves the developer's resolved state directory untouched.
- **An install written before 0.4.0 never received the 0.4.0 frontmatter fix.** The build stamp hashed only the rules body, so the description change and the provenance change could not make an older file read as stale: the body still matched its stamp, and both doctor and the reminder hook reported no drift. The stamp now covers the whole rendered file, its own line excepted, so every byte the renderer produces takes part in the comparison — including a pre-0.4.0 install's own, whose body-only stamp can no longer equal a whole-file hash. Those files read as stale on their own and name `copydesk setup --repair` as the way forward.
- **Claude Code's line in the setup list understated what setup writes.** The wizard prints an `installs` summary beside each harness while the user picks what to configure, and Claude Code's said output styles and two hooks although setup since 0.4.0 also writes a block into `CLAUDE.md`. The summary now names all three.

## [0.4.0]

### Fixed

- **Every harness was missing channels the others got.** The instruction block joined documents, commits and reviews, so the six non-Claude harnesses never saw the chat channel or the behavioural clauses no style choice can remove — while Claude Code, whose file the wizard skipped outright, never saw documents, commits or reviews at all. A machine could look correct only by accident, when a symlink from another harness landed their block inside Claude Code's file. The registry now names each harness's instruction file, one function renders the block with a flag for whether chat belongs in it, and what a file carries is decided per real file after symlinks resolve: where two harnesses share one file, one write lands carrying all four channels, at the cost of about 340 duplicated words against the output style. Uninstall follows the same registry field, so Claude Code's file is taken back too.
- **A disabled chat channel still filled every non-Claude instruction file.** `render_agents_block` joined chat on the `include_chat` flag alone, and `render_chat` never reads `channels.chat.enabled`. Documents, commits and reviews already return empty when off. Chat now does the same at the join, not inside `render_chat`, because the output style still calls that renderer for its rules region.
- **An installed output style advertised the wrong style.** The frontmatter description came from the preset's own block whatever the config chose, so an install set to `engineer` told Claude Code's style picker it was plain while the body below rendered terse engineer prose. The description now follows the configured chat style, read from the same shelf the picker text comes from.
- **Every installed output style named the repository's generator as its writer.** Setup writes those copies from the user's config, and they differ from what the repository ships. The provenance comment now takes a writer: the generator names itself on shipped copies, and an installed copy names `copydesk setup` and points at `copydesk setup --repair` as the way to regenerate it.

## [0.3.1]

### Fixed

- **The instruction block contradicted itself about commit bodies.** A channel line, a style line and a verbosity line were joined unconditionally, so `commits` at style `engineer` and verbosity `medium` asked for bullets and a paragraph at once, and verbosity `high` asked for what changed after the channel line had said not to. Verbosity lines now speak about extent alone.
- **Every `reviews` style line restated its channel line.** The channel line names the file, the line and the fix; all four style lines said the same words again before adding their own. They now carry only the form the review comment takes.

## [0.3.0]

### Added

- **`copydesk hook add|remove|list`.** The commit-msg hook is the one thing CopyDesk installs outside the home directory, and setup only ever touched the repository it ran from. The new subcommand manages the hook across repositories: `hook add` installs into the current or named repositories, `hook add --scan <dir>` offers every repository one level under a directory, `hook list` reports each recorded repository, and `hook remove [--all]` takes them back.
- **A registry of hooked repositories** at `$XDG_STATE_HOME/copydesk/hooks.json`. It is a hint, never the truth: every read opens the hook file and looks for the marker, and an entry whose repository or hook is gone is pruned. Writes go through a temporary file and a rename under the state directory's lock.
- **Chaining into a foreign commit-msg hook.** A hook someone else wrote is never overwritten. `hook add` offers to append a marked block instead, verifies with a test run that the block is reached — a hook ending in `exit 0` swallows whatever follows it — and records the outcome as `chained`, `unreachable`, or `skipped`. Removal strips the marked region and leaves the rest of the script untouched.
- **Setup and uninstall join the registry.** `copydesk setup` records the repository it installs into and names `copydesk hook add` for the others. `copydesk uninstall` asks once about the other recorded repositories, defaults to yes, and prints their hook paths when declined — enough to remove each hook by hand once no CopyDesk command is available.

### Fixed

- **The state sweeper deleted the hook registry.** `hooks.json` sits beside the retry session files, and a blocking gate run unlinks every `*.json` there that is a day old. The registry went with them, so `hook list` and uninstall's other-repository cleanup read empty while the hooks stayed on disk. The sweeper now skips that one name, and `hook.py` builds its path from the same constant.
- **The chained block refused commits inside a hook that sets `-e`.** Errexit ends the script at the failing command, so `"$COPYDESK" check …; status=$?` never reached the line reading the status. An internal error or a missing CopyDesk then refused the commit instead of failing open. The block now captures the status through `|| status=$?`, and guards a missing binary before calling it, as `git-hooks/commit-msg` does.
- **Uninstall claimed success over a block it could not strip.** A start marker whose region does not match leaves the script alone, which `hook remove` reports. Uninstall ignored that answer and printed `Uninstall complete.` anyway. It now names the file, says the lines are still there, and exits non-zero.
- **The chained block aborted commits that CopyDesk passed.** The block is appended last, so its final `[ "$status" -gt 1 ] && echo …` line set the hook's exit status, and an AND-OR list whose tests are false returns 1. The block now uses explicit `if` statements, captures the foreign script's own exit status first, and exits with it.
- **`hook remove` could delete a foreign hook whole.** A block pasted with indentation matched the start marker but not the stripping region, and control fell through to unlinking the file. The region now tolerates leading whitespace, and removal never deletes a file whose start marker it cannot strip.
- **`resolve()` ignored a caller that asked for no user configuration.** `user_path=None` meant "go and find it" rather than "read none", so twenty-six call sites that opted out were handed the file anyway. `None` now skips that layer and a `DISCOVER` sentinel is the default.
- **The instruction generator read the contributor's own configuration.** On a machine with CopyDesk installed, `scripts/generate-instructions.py --check` failed, and running the generator would have written personal settings into the three committed output styles. It now resolves the preset alone.

## [0.2.3]

### Fixed

- **Escape inside a Customize question crashed the wizard.** The style, verbosity and guidance questions had no handler for it, so `Cancelled` reached the top and Python printed a traceback. Escape now returns to the question before it, which is what the key bar has always said it does.
- **Escape went nowhere useful even where it did not crash.** The six questions that caught it ended setup outright. Escape now moves back one question everywhere, and cancels only at the first question, where there is nothing behind it.

### Changed

- **Git has its own question.** It was the eighth entry in a list of AI tools, mixing two different things: the other seven configure an assistant in your home directory, while git installs a commit-msg hook into whichever repository you run setup from. The new question says so: `Check your commit messages in this repository too?`
- **Going back and forward again keeps your answer.** Each question offers what you chose last time as its default rather than resetting.

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
