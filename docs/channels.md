# Channels

CopyDesk routes agent prose into four distinct channels. Each channel has its own delivery mechanism, default style, and gate enforcement.

## The four channels

| Channel | Medium | Gate enforcement | Default style | Default verbosity |
|---|---|---|---|---|
| `chat` | Conversational output in terminal | Prevention only (context instructions and reminder précis) | `plain` | `low` |
| `documents` | Markdown files written to disk | `PreToolUse` write/edit hook | `plain` | `high` |
| `commits` | Git commit messages | `commit-msg` git hook | `engineer` | `low` |
| `reviews` | Review comments and markdown feedback | Matching file paths via `channels.reviews.match` | `plain` | `medium` |

## Why channels exist

Different media require different writing styles:
- Terminal chat needs fast, direct answers without unnecessary filler.
- Architecture documents require structured procedures or narrative reasoning.
- Git commit messages require imperative subjects under 72 characters and clear rationale.
- Code reviews require specific line references and actionable corrections.

A single global style cannot fit all four channels. Channels allow setting styles and rules per medium.

## Fixed claim order

When determining which channel claims a file, CopyDesk evaluates paths in a fixed order:

1. **`reviews`**: Matches files configured under `channels.reviews.match` (e.g. `docs/reviews/**`, `*.review.md`).
2. **`documents`**: Matches any remaining Markdown file (`*.md`, `*.markdown`) unless ignored by paths configuration.
3. **`commits`**: Handled via `copydesk check --commit-msg` or git hooks.
4. **`chat`**: Handled via output styles and session prompt injection.

Non-Markdown files (such as `.py`, `.ts`, `.json`) are not claimed by any channel and pass through without linting.

## Path routing and glob syntax

The `paths` section in configuration allows fine-grained control over file matching:

```json
{
  "version": 1,
  "paths": {
    "ignore": [
      ".workbench/**",
      "drafts/**",
      "node_modules/**"
    ],
    "warn": [
      "CHANGELOG.md",
      "docs/adrs/*.md"
    ],
    "block": [
      "docs/**",
      "README.md"
    ]
  }
}
```

### Action precedence

Within a layer, actions resolve from most specific to least specific:
1. `ignore`: Skips checking completely.
2. `warn`: Reports findings on standard error, but exits 0 and never blocks a write.
3. `block`: Reports findings and blocks writes with exit code 1 if errors are found.

Later configuration layers (project config over user config) override earlier layers.

### Glob rules
- Globs in a project or local file match relative to that file's own directory.
- Globs in the user file match the absolute path, because a user file names no directory.
- `**` matches across directory boundaries.
- `*` matches within a single path segment.
- Negative patterns starting with `!` can re-include paths ignored by broader patterns.

## Channel configuration in `copydesk.config.json`

Each channel can configure its style, verbosity, and specific guidance deliverables:

```json
{
  "version": 1,
  "channels": {
    "chat": {
      "style": "plain",
      "verbosity": "low",
      "guidance": {
        "direction": true,
        "progress": true,
        "alternatives": false
      }
    },
    "documents": {
      "style": "engineer",
      "guidance": {
        "summary": true,
        "verification": true
      }
    },
    "commits": {
      "style": "engineer"
    },
    "reviews": {
      "enabled": true,
      "style": "plain",
      "match": ["docs/reviews/**", ".github/reviews/**"]
    }
  }
}
```

If `channels.reviews` has no `match` globs defined, `reviews` operates in prevention-only mode.
