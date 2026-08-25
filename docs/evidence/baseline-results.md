# Gate baseline — measured Claude Code sessions

Measured 2026-08-17, before the rule set changed. It records what an agent's chat looked like turn by turn across two continuous ten-turn sequences.

[`eval/results/baseline-results.md`](../../eval/results/baseline-results.md) is the current baseline. It re-measures these transcripts with the 2026-08-24 word counter. The blocking counts are identical; the median moves from 8.09 to 8.48 because fewer words qualify.

The point of this page is the controls. A prose measurement with no record of model, effort and target is not reproducible, and most published claims about AI writing carry none.

## Controls

Recorded in [`eval/results/controls.json`](../../eval/results/controls.json).

| Control | Value |
|---|---|
| Harness | Claude Code 2.1.233 |
| Model | `claude-opus-5[1m]` |
| Effort | `high` |
| Approval mode | `auto` |
| Active output style | the `plain-english` preset |
| Target checkout | [`carlosboeing/crossrev`](https://github.com/carlosboeing/crossrev) pinned at commit `c72d978` |

The target is a public repository at a pinned commit, so the same sequences can be run again against the same code.

## Method

Two continuous ten-turn sequences, each a single session rather than ten separate prompts:

- `01-implementation-dry-run`
- `03-debugging-auth`

A third sequence and a synthetic corpus were dropped. Multi-turn sessions at this model and effort cache roughly 150,000 tokens per turn, and the runs kept hitting five-hour rate limits. Two full sequences measured properly beat six sequences measured badly.

Continuity matters here. The question is whether style persists across a long session, and ten separate prompts cannot answer it.

## Checkpoint summary

Blocking violations per 1,000 qualifying words, in the agent's visible chat.

| Sequence | Turn 1 | Turn 5 | Turn 10 |
|---|---:|---:|---:|
| `01-implementation-dry-run` | 9.20 | 6.93 | 7.38 |
| `03-debugging-auth` | 9.13 | 7.96 | 8.80 |
| **Median** | **9.17** | **7.45** | **8.09** |

Both sequences start near the historical chat rate of 9.14, drop by turn 5, and rise again by turn 10 without returning to where they began. Decay across a long session is real and partial.

A partial rebound is the argument for re-sending a short précis every turn, rather than relying on instructions given once.

## Sequence `01-implementation-dry-run`

Session `87f3870d-b81d-4eaa-a95e-c295710c4da9`.

| Turn | Qualifying words | Blocking violations | Per 1,000 words | Warnings | Blocking rules |
|---|---:|---:|---:|---:|---|
| 01 | 761 | 7 | 9.20 | 6 | `announcing-opener` 1, `long-sentence-rate` 1, `sentence-length` 2, `banned-word` 2, `paragraph-length` 1 |
| 02 | 577 | 3 | 5.20 | 8 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1 |
| 03 | 683 | 3 | 4.39 | 7 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1 |
| 04 | 830 | 5 | 6.02 | 9 | `long-sentence-rate` 1, `sentence-length` 2, `banned-word` 1, `orphan-pointer` 1 |
| 05 | 577 | 4 | 6.93 | 5 | `announcing-opener` 1, `long-sentence-rate` 1, `sentence-length` 1, `orphan-pointer` 1 |
| 06 | 221 | 3 | 13.57 | 2 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1 |
| 07 | 429 | 3 | 6.99 | 8 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1 |
| 08 | 843 | 2 | 2.37 | 7 | `sentence-length` 1, `banned-word` 1 |
| 09 | 426 | 0 | 0.00 | 1 | none |
| 10 | 542 | 4 | 7.38 | 6 | `announcing-opener` 1, `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1 |

Turn 06 scores as the worst turn at 13.57 and is the shortest at 221 words. A per-1,000-word rate over a short turn is noisy, which is why the checkpoint summary reports turns 1, 5 and 10 rather than a single peak.

### Markdown written during the sequence

| Turn | Qualifying words | Blocking violations | Per 1,000 words | Warnings |
|---|---:|---:|---:|---:|
| 07 | 376 | 3 | 7.98 | 4 |
| 09 | 321 | 3 | 9.35 | 4 |

## Sequence `03-debugging-auth`

Session `11be2091-aaa2-47db-9f9b-3d4180fd2129`.

| Turn | Qualifying words | Blocking violations | Per 1,000 words | Warnings | Blocking rules |
|---|---:|---:|---:|---:|---|
| 01 | 657 | 6 | 9.13 | 10 | `long-sentence-rate` 1, `orphan-pointer` 2, `banned-word` 1, `paragraph-length` 2 |
| 02 | 772 | 5 | 6.48 | 6 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 2, `paragraph-length` 1 |
| 03 | 877 | 6 | 6.84 | 16 | `long-sentence-rate` 1, `sentence-length` 2, `banned-word` 1, `orphan-pointer` 1, `paragraph-length` 1 |
| 04 | 561 | 4 | 7.13 | 5 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1, `paragraph-length` 1 |
| 05 | 377 | 3 | 7.96 | 4 | `long-sentence-rate` 1, `banned-word` 1, `paragraph-length` 1 |
| 06 | 637 | 4 | 6.28 | 5 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1, `paragraph-length` 1 |
| 07 | 793 | 6 | 7.57 | 11 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 2, `paragraph-length` 2 |
| 08 | 778 | 8 | 10.28 | 7 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 2, `orphan-pointer` 1, `paragraph-length` 3 |
| 09 | 342 | 4 | 11.70 | 5 | `long-sentence-rate` 1, `banned-word` 2, `paragraph-length` 1 |
| 10 | 682 | 6 | 8.80 | 10 | `long-sentence-rate` 1, `sentence-length` 1, `banned-word` 1, `paragraph-length` 3 |

`long-sentence-rate` fires on every single turn of this sequence. A debugging session produces long explanatory sentences, and the rule is the one that catches them.

### Markdown written during the sequence

| Turn | Qualifying words | Blocking violations | Per 1,000 words | Warnings |
|---|---:|---:|---:|---:|
| 06 | 210 | 2 | 9.52 | 2 |
| 07 | 236 | 1 | 4.24 | 3 |

## What this does not show

These are two sequences from one operator, one harness and one model. They measure whether style decays across a long session, and they do. They do not establish a rate for anyone else's agent, and the numbers here should not be quoted as one.

The [observational baseline](observational-baseline.md) covers a far larger corpus and answers the different question of what the prose looked like historically.
