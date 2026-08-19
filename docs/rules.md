# Rules

Every rule is listed with its group, what it takes, and the severity the `plain-english` preset ships it at.

Severity is always yours to change. `error` blocks a write, `warn` reports it, `off` disables the rule.

## Pattern rules

Token lists, held as data in `rules/plain-english.json`. Every user wants to differ here, so these are the rules a config edits most.

| Rule | Severity | Scope | Tokens | What it catches |
|---|---|---|---:|---|
| `orphan-pointer` | `error` | word | 6 | Pointers that make the reader hunt for an earlier part |
| `banned-word` | `error` | word | 28 | Opaque jargon, filler intensifiers and AI-tells |
| `verb-jargon` | `warn` | word | 5 | Nouns used as verbs where a plainer verb exists |
| `contrast-construction` | `error` | raw | 1 | The "not just X, it's Y" shape, which adds no information |
| `soft-offer` | `error` | word | 9 | Offers that ask nothing answerable |
| `announcing-opener` | `error` | line-initial | 13 | Openers that announce rather than answer |
| `idiom` | `error` | word | 4 | Figurative phrases that hide the literal action |
| `orphan-pointer` | `error` | line-initial | 1 | Pointers that make the reader hunt for an earlier part |

67 tokens across 8 blocks. A rule appears more than once when it carries tokens at different scopes or severities.

Tokens are regular expressions, matching Vale's `existence` check. A plain word is a regular expression matching itself, so adding one needs no regex knowledge.

## Metric and structural rules

Code, with their parameters exposed. Thresholds and word lists vary between projects; the algorithms do not.

| Rule | Group | Parameters | Shipped severity |
|---|---|---|---|
| `sentence-length` | metric | `max` 25 words warns, `hard_max` 40 words blocks | warn / error |
| `paragraph-length` | metric | `max_sentences`, default 4 | error |
| `avg-sentence-length` | metric | `min` 12, `max` 20 words | warn |
| `long-sentence-rate` | metric | `max_rate`, default 0.10 | error |
| `sentence-variation` | metric | `min_stdev`, default 4.0 | warn |
| `list-dominated` | metric | `exemption_ratio`, default 0.5 | off, an exemption rather than a finding |
| `unglossed-term` | metric | `vocabulary.add`, merged across all three files | warn |
| `nested-table` | structural | toggle only | error |

### Notes on three of them

**`long-sentence-rate` is the one blocking rule computed over the whole document.** It is reported at line 1, so it cannot be attributed to an edited region. On an Edit it blocks when it newly fires: absent before the edit and present after.

**`list-dominated` never produces a finding.** It is an exemption predicate: a document more than half list lines skips the document-level statistics. Changing `exemption_ratio` changes which documents are exempt.

**`unglossed-term` is a heuristic, not a token list.** First-use detection, gloss detection and the skip rules for code, links and headings are algorithm. Only the vocabulary is configuration. It is recorded under `rules` and never under `patterns`, so a later port cannot mistake it for data.

## The vocabulary, three lists

`unglossed-term` reads one field path, `rules.unglossed-term.vocabulary.add`, from three files.

| List | Scope | File |
|---|---|---|
| Shipped | Terms nobody glosses anywhere | `rules/<preset>.json` |
| Personal | Terms you use everywhere | your user config |
| Project | Terms this repository established | `copydesk.config.json`, committed |

The project file wins, because the repository's audience is the constraint. A contributor's personal vocabulary must not quietly loosen the standard a project publishes under.

The `plain-english` preset ships 32 universal terms, each recorded under a stated reason so you can challenge one entry rather than the list.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No findings, or warnings only |
| 1 | Findings reported |
| 2 | A hook blocked the write |
| 64 | Usage error |
