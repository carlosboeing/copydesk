# Configuration

CopyDesk reads JSON and JSON with comments (JSONC). Settings merge across discovery locations.

## Where configuration lives

CopyDesk discovers and merges configuration from three locations:

| Layer | Location | Purpose |
|---|---|---|
| **User** | `$XDG_CONFIG_HOME/copydesk/config.json` (or `~/.config/copydesk/config.json`) | Personal defaults and global vocabulary |
| **Project** | `copydesk.config.json` (discovered walking up from target file) | Shared repository standards, committed to git |
| **Local** | `copydesk.local.json` (walked up from target file) | Personal project overrides, git-ignored |

Discovery walks up from the document being checked until the root or git boundary is reached.

## Resolution order

1. Built-in base preset (`plain`).
2. Channel style preset (e.g. `engineer` for documents).
3. Presets named in `extends`.
4. User file.
5. Project file.
6. Local file.

Later layers override earlier ones. Personal keys in a project file are ignored with a warning so contributor machines do not alter shared standards.

## Full configuration schema

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/carlosboeing/copydesk/v0/copydesk.schema.json",
  "version": 1,
  "extends": "plain",

  // Target harnesses to configure
  "agents": ["claude-code"],

  // Channel configuration
  "channels": {
    "chat": {
      "style": "plain",
      "verbosity": "low",
      "guidance": {
        "direction": true,
        "progress": true
      }
    },
    "documents": {
      "style": "engineer",
      "verbosity": "high"
    },
    "commits": {
      "style": "engineer"
    },
    "reviews": {
      "enabled": true,
      "style": "plain",
      "match": ["docs/reviews/**"]
    }
  },

  // Path routing and action overrides
  "paths": {
    "ignore": [".workbench/**", "node_modules/**"],
    "warn": ["CHANGELOG.md"],
    "block": ["docs/**"]
  },

  // Gate retry limit
  "gate": {
    "retries": 3
  },

  // Telemetry recording
  "telemetry": {
    "events": true,
    "saveText": false
  },

  // Specific rule threshold overrides
  "rules": {
    "sentence-length": {
      "severity": "warn",
      "max": 25,
      "hardMax": 40
    },
    "banned-word": {
      "severity": "error",
      "add": ["synergy"],
      "remove": ["solid"]
    },
    "unglossed-term": {
      "severity": "warn",
      "add": ["React", "Postgres"]
    },
    "nested-table": {
      "severity": "off"
    }
  }
}
```

## Parameter casing and aliases

Parameters use camelCase by default. Existing snake_case aliases continue to work:

| Canonical camelCase | Compatibility alias | Applied rule |
|---|---|---|
| `hardMax` | `hard_max` | `sentence-length` |
| `maxSentences` | `max_sentences` | `paragraph-length` |
| `maxRate` | `max_rate` | `long-sentence-rate` |
| `minStdev` | `min_stdev` | `sentence-variation` |
| `exemptionRatio` | `exemption_ratio` | `list-dominated` |
| `add` | `vocabulary.add` | `unglossed-term` |

## Severity levels

| Value | Effect |
|---|---|
| `error` | Blocks writes through the gate, exits 1 on check |
| `warn` | Emits a warning on stderr, does not block (exits 0) |
| `off` | Rule is disabled completely |

## Word lists

Word lists on pattern rules and `unglossed-term` use `add` and `remove` arrays. This allows tailoring word lists without copying the entire base list.
