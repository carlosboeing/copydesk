# Styles and Floor

CopyDesk provides a shelf of writing styles, universal behavioral floor rules, and composition mechanisms via presets and channels.

## The style shelf

CopyDesk ships four core styles:

| Style | Character | Best suited for |
|---|---|---|
| `plain` | Answer first, clear assertions, structured lists when helpful | Day-to-day coding, technical documentation, design docs |
| `engineer` | Terse procedures, numbered steps, tables, minimal prose | API references, runbooks, schemas, operational guides |
| `editorial` | Narrative explanation, complete paragraphs, rare lists | Thought leadership, blog posts, long-form reviews |
| `general` | Plain terms with every specialized term glossed | Public onboarding guides, non-technical stakeholder docs |

## How styles render across channels

Each style defines distinct guidelines depending on the target channel:

| Style | Chat behavior | Documents behavior | Commits behavior |
|---|---|---|---|
| `plain` | Short sentences; structure where helpful, prose where not | Prose carries reasoning; structure carries facts | Imperative subject, body as clean prose |
| `engineer` | Terse lists and tables; one instruction per sentence | Numbered procedures and tables; minimal connecting prose | Imperative subject, body facts as bullets |
| `editorial` | Flowing short paragraphs; lists and tables are rare | Prose almost everywhere; structure is rare and deliberate | Imperative subject, narrative rationale in body |
| `general` | Short sentences; gloss every specialized term | Commonest words; explain unfamiliar concepts inline | Imperative subject, every domain term glossed |

## The behavioral floor

All styles share a non-negotiable floor that governs agent interactions:

1. **Answer first**: Provide the core conclusion or answer immediately before offering supporting context or details.
2. **Closing block reserved**: Only questions and choices requiring reader action belong at the end. Things the agent is doing are never repeated in the closer.
3. **Say once**: No restating what was just completed, no process narration, and no recaps between turns.
4. **No conversational filler**: Prohibits conversational openers like "Sure!" and soft closing offers like "Hope this helps!".

The floor is immune to style loosening; it applies universally across all configured presets.

## Guidance deliverables

Guidance flags enable specific structural deliverables within instructions:

| Guidance ID | Directive |
|---|---|
| `recommendations` | When a choice is open, provide a proposed answer and one reason. Never present options without a pick. |
| `direction` | When work continues, name the next step. Never end on a step you are about to take yourself. |
| `progress` | State multi-step progress in one line (e.g. `Step 3 of 5 done, next is...`). Never list already completed items. |
| `pushback` | Before agreeing with a challenged premise, offer the strongest counter-argument. |
| `alternatives` | Rank alternatives with one line of trade-offs each, recommendation first. |
| `assumptions` | State operative assumptions before starting work, not after. |
| `estimates` | Give estimates in concrete units (e.g. `15 minutes`, `two days`). Never say `some work`. |
| `sources` | Put sources beside factual claims (file/line for code, URL for web). |
| `summary` | Open long documents with a three-sentence abstract. |
| `verification` | When claiming completion, describe how it was tested. |

## Composing styles with `extends`

You can extend presets and override specific rules in `copydesk.config.json`:

```json
{
  "version": 1,
  "extends": "plain",
  "channels": {
    "chat": {
      "style": "plain",
      "verbosity": "low"
    },
    "documents": {
      "style": "engineer"
    }
  },
  "rules": {
    "sentence-length": { "max": 20 },
    "banned-word": { "add": ["leverage"] }
  }
}
```

The cascade order:
1. Base preset (`rules/plain.json`).
2. Channel-specific style preset (`rules/engineer.json`).
3. Additional presets named in `extends`.
4. User configuration (`~/.config/copydesk/config.json`).
5. Project configuration (`copydesk.config.json`).
6. Local configuration (`copydesk.local.json`).
