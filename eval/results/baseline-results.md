# Prevention baseline

Published 2026-08-24. This file is the source `copydesk report` names in its Prevention section. It holds the corpus blocking rate and the per-rule false-positive rates that two pending decisions depend on: the reminder précis cost and the `unglossed-term` severity. No rule, threshold or severity changes here; the decisions are separate work.

## Corpus rate

The command below re-measured the captured baseline transcripts under the rules of 2026-08-24. `--measure-only` launches no sessions: it re-runs the measurement phase over any results root holding previously captured transcripts. The transcripts themselves were produced by an earlier confirmed Claude Code run against the pinned target commit, under the controls recorded in `controls.json`.

    $ time bash eval/run-corpus.sh --measure-only --harness claude --condition baseline \
          --results-root <captured-transcripts-root>
    summary: rate=8.48 across 2 sequence runs (baseline/claude)
    real     0m0.123s

| Sequence | Final turn | Words | Blocking findings | Rate |
|---|---:|---:|---:|---:|
| `01-implementation-dry-run` | 10 | 511 | 4 | 7.83 |
| `03-debugging-auth` | 10 | 657 | 6 | 9.13 |
| **Median** | | | | **8.48** |

Rate is blocking findings per 1,000 qualifying chat words at the final turn. The full per-run detail sits beside this file in `2026-08-24-summary.json`. The prior summary (`2026-08-17-summary.json`, rate 8.09) measured the same transcripts under the rules of its own day; both stay, and `report` reads the newest.

## False-positive criterion

A finding is a false positive when the flagged text is correct as written: an editor would not change it for the reason the rule gives. Families were judged against that single standard, with the defect read from each rule's definition in `rules/plain.json`:

- Metric and structural rules (`sentence-length`, `paragraph-length`, `avg-sentence-length`, `long-sentence-rate`, `sentence-variation`). True when the span burdens a reader. False when the text is ordered prose, a list-shaped enumeration, or a sentence stating a measurement.
- Lexical rules (`banned-word`, `verb-jargon`, `idiom`, `soft-offer`, `announcing-opener`, `contrast-construction`). Mention differs from use: a document quoting a banned term as an example is not using it.
- `unglossed-term`. False when the term is common vocabulary for a developer audience. True when a coinage reaches the reader without a gloss.
- `orphan-pointer`. False when the demonstrative resolves within the stored excerpt or quotes the ban list itself.

### Sample

Stored finding excerpts from 2,929 lint events recorded 2026-08-18 through 2026-08-24 on one machine. Texts were deduplicated per rule, then up to 30 per rule were drawn by a seeded shuffle. Two exclusion classes leave the denominator, with their counts given per rule below:

1. Synthetic payloads from this repository's own test fixtures. They violate by construction and say nothing about real writes.
2. Excerpts whose firing token, or whose demonstrative's referent, falls outside the stored text. The record does not support a verdict either way.

### Rates

| Rule | Window findings | Judged n | True | False | FP rate | Dominant false class | Excluded |
|---|---:|---:|---:|---:|---:|---|---:|
| `sentence-length` | 22,094 | 29 | 2 | 27 | 93% | long but parallel or list-shaped sentences | 1 |
| `banned-word` | 4,938 | 13 | 6 | 7 | 54% | quoted examples; contrastive `actually`/`genuinely` | 17 |
| `unglossed-term` | 3,662 | 29 | 2 | 27 | 93% | product and tooling nouns a developer reads daily | 1 |
| `verb-jargon` | 2,740 | 15 | 0 | 15 | 100% | noun `surface`; natural arrival sense of `land` | 15 |
| `announcing-opener` | 1,921 | 0 | – | – | – | all stored unique texts were fixtures | 3 |
| `paragraph-length` | 419 | 30 | 0 | 30 | 100% | flattened lists; dense but ordered spec prose | 0 |
| `avg-sentence-length` | 250 | 30 | 0 | 30 | 100% | fires on CopyDesk's own stats and report lines | 0 |
| `orphan-pointer` | 211 | 13 | 0 | 13 | 100% | resolving pointers; quoted ban lists | 17 |
| `sentence-variation` | 70 | 3 | 0 | 3 | 100% | own-report lines | 0 |
| `soft-offer` | 58 | 1 | 1 | 0 | 0% | – | 0 |
| `contrast-construction` | 29 | 1 | 0 | 1 | 100% | meaningful contrast | 0 |
| `idiom` | 25 | 1 | 1 | 0 | 0% | – | 0 |
| `long-sentence-rate` | 24 | 17 | 0 | 17 | 100% | own-report lines | 0 |

### Notes the decisions should carry

- The three aggregate rules (`avg-sentence-length`, `long-sentence-rate`, `sentence-variation`) fired almost only on CopyDesk's own stats and report Markdown passing back through the gate. Every judged hit was the metric line itself. That is a measurement loop, not writer behaviour.
- `verb-jargon`'s pattern matches the noun `surface`, which this project's documentation uses as a countable term of art. Fifteen of fifteen judged hits were that noun or an ordinary use of `land`.
- `unglossed-term` spends its warnings on proper nouns (`Claude`, `Codex`, `Bash`, `README`, `OpenAI`). Its two true catches were project coinages reaching the page without a gloss.
- `banned-word` earns its warning budget: six of thirteen judged hits were earnest uses of jargon the ban targets.
- `announcing-opener` lacks a natural-writing sample in this window. A severity decision for it needs a fresh sample from real sessions.
