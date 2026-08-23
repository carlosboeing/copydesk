# Rules

Every rule is listed with its group, configurable parameters, and default severity.

## Pattern rules

Data-driven rules matched against token lists:

| Rule | Severity | Scope | What it catches |
|---|---|---|---|
| `orphan-pointer` | `error` | word / line-initial | Relative pointers that force re-reading earlier text |
| `banned-word` | `error` | word | Opaque jargon, filler intensifiers, and machine-tells |
| `verb-jargon` | `warn` | word | Nouns used as verbs where plainer alternatives exist |
| `contrast-construction` | `error` | raw | The contrast pattern (`not just X, it's Y`) |
| `soft-offer` | `error` | word | Vague closing offers that ask no specific question |
| `announcing-opener` | `error` | line-initial | Opening sentences that announce instead of answering |
| `idiom` | `error` | word | Figurative phrases that obscure concrete actions |

## Metric and structural rules

Algorithmic rules evaluating length, structure, and vocabulary:

| Rule | Group | Parameters (canonical / alias) | Default severity |
|---|---|---|---|
| `sentence-length` | metric | `max` (default 25), `hardMax` / `hard_max` (default 40) | `warn` / `error` |
| `paragraph-length` | metric | `maxSentences` / `max_sentences` (default 4) | `error` |
| `avg-sentence-length` | metric | `min` (default 12), `max` (default 20) | `warn` |
| `long-sentence-rate` | metric | `maxRate` / `max_rate` (default 0.10) | `error` |
| `sentence-variation` | metric | `minStdev` / `min_stdev` (default 4.0) | `warn` |
| `list-dominated` | metric | `exemptionRatio` / `exemption_ratio` (default 0.5) | `off` (exemption predicate) |
| `unglossed-term` | metric | `add` / `vocabulary.add` | `warn` |
| `nested-table` | structural | toggle only | `error` |

## Notes on specific rules

- **`long-sentence-rate`**: Evaluated across the entire file. Blocks on edit when the edit introduces a rate violation.
- **`paragraph-length`**: Evaluated per paragraph. Blocks on edit when the edit touches the sentences the rule counted, whether the paragraph was already over the cap or not. List item lines are excluded from the count and from that comparison, so rewording a bullet inside an over-long block does not block.
- **`list-dominated`**: Acts as an exemption filter. Documents composed predominantly of lists skip paragraph and sentence variation checks.
- **`unglossed-term`**: Flags capitalized terms without nearby explanations on their first use. Merges terms across base preset, user configuration, and project configuration.

## Vocabulary lists for `unglossed-term`

| List | Scope | File source |
|---|---|---|
| Shipped | Universally recognized terms | `rules/plain.json` |
| Personal | User-wide allowed terms | `~/.config/copydesk/config.json` |
| Project | Repository-specific terms | `copydesk.config.json` |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean, or warnings only |
| 1 | Blocking prose findings |
| 2 | Hook blocked a write |
| 64 | Usage error |
| 70 | Internal error (fail-open) |
