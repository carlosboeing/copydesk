# Configuration

CopyDesk reads JSON. Two files are discovered and merged rather than chosen between.

## Where config lives

| Layer | Location |
|---|---|
| User | `$XDG_CONFIG_HOME/copydesk/config.json`, falling back to `~/.config/copydesk/config.json` |
| Project | the nearest `copydesk.config.json`, walking up from the document being linted |

Both are optional. With neither, the built-in `plain-english` preset applies.

## Resolution order

Built-in preset, then each preset named by `extends` in array order, then the user file, then the project file. Later entries win.

The project file sits last on purpose. The repository's audience is the constraint, so a contributor's personal vocabulary must not quietly loosen the standard a project publishes under. Git and every linter use the same precedence.

## Shape

```json
{
  "version": 1,
  "extends": "plain-english",
  "rules": {
    "sentence-length": { "severity": "warn", "max": 30 },
    "banned-word": {
      "severity": "error",
      "add": ["synergy", "ideate"],
      "remove": ["robust"]
    },
    "unglossed-term": {
      "severity": "warn",
      "vocabulary": { "add": ["React", "Postgres"] }
    },
    "nested-table": { "severity": "off" }
  }
}
```

`version` is required. Without it no schema migration is possible, so a file missing it is refused rather than guessed at.

`extends` accepts a string or an array of strings. An explicit `rules` block overrides everything.

## Severity

Three values, frozen from the first release.

| Value | Effect |
|---|---|
| `error` | blocks a write through the gate |
| `warn` | reported, never blocks |
| `off` | the rule does not run |

A fourth value later would be a migration, not an addition.

## Word lists are additive

`add` and `remove`, never replacement. Replacement would make extending a preset require restating it, which is how style configs rot.

```json
{ "banned-word": { "add": ["synergy"], "remove": ["robust"] } }
```

`add` and `remove` apply to pattern rules. Using them on a metric rule such as `sentence-length` is an error rather than a silent no-op, because a silently ignored key hides a typo.

## The preset document

A preset is one file holding both its pattern rules and its settings.

```json
{
  "version": 1,
  "id": "plain-english",
  "patterns": [
    {
      "id": "banned-word",
      "kind": "existence",
      "scope": "word",
      "severity": "error",
      "message": "Say the plain thing instead of \"%s\".",
      "ignorecase": true,
      "tokens": ["robust", "comprehensive", { "phrase": "delve", "match": "delv\\w*" }]
    }
  ],
  "rules": {
    "sentence-length": { "severity": "warn", "max": 25 }
  }
}
```

`patterns` carries the data rules. `rules` carries severities and parameters for every rule, pattern or code alike, in exactly the shape a user config uses. A preset and a config file therefore differ by one key.

A token is a bare string when it is its own pattern, and an object carrying `phrase` and `match` when they differ. Tokens are regular expressions, matching Vale's `existence` check, so a plain word works with no regex knowledge and a stem like `delv\w*` is still expressible.

`scope` is `word`, `line-initial` or `raw`. Metric and structural rules never appear in `patterns`; configuration reaches them through `rules`.

## Errors

Every one of these refuses rather than guessing.

| Problem | Why it is an error |
|---|---|
| Two config files in one directory | Format precedence was the bug, not the fix. There is no search order between formats. |
| A `copydesk.config.yaml` | This build reads JSON only. A stray YAML file must not hide a valid JSON one. |
| A missing or unrecognised `version` | Without it no schema migration is possible. |
| Malformed JSON | The message names the line and column. |
| An unreadable file | The message names the reason. |
| `extends` naming a preset that does not exist | The message lists the presets that do. |
| A severity outside the three values | The message names the value and the three allowed. |
| `add` or `remove` on a metric rule | Word lists have no meaning there, and ignoring the key would hide a typo. |

## The gate fails open

Every error above is reported and then ignored. CopyDesk prints one line naming the problem, records a telemetry event, and lints with the built-in preset.

A hook that blocks on its own misconfiguration is worse than one that lets the write through. Reporting rather than staying silent is what stops a mistyped filename from quietly disabling checking.

The error is printed once per session rather than once per document.

## Environment variables

| Variable | Effect |
|---|---|
| `COPYDESK_RULES` | Path to the preset document. Defaults to `rules/plain-english.json` beside the bundle, then beside `linter.py`. |
| `COPYDESK_STATE_DIR` | Overrides the state directory. |
| `COPYDESK_LOG` | `0` disables telemetry writing entirely. |
| `COPYDESK_LOG_FLAGGED_TEXT` | `0` records rule names and line numbers while omitting the flagged text. |

The state directory defaults to `$XDG_STATE_HOME/copydesk/`, falling back to `~/.local/state/copydesk/`.

## Why JSON first

Python's standard library parses JSON with no dependency, which the gate hook requires on a path that runs on every Write and Edit. PyYAML is not in the standard library, and `tomllib` needs Python 3.11 or later.

Accepting more formats later is backwards-compatible. Dropping one is not. When YAML arrives, JSON keeps working and the two-files rule still holds.
