# Observational baseline

Measured 2026-08-17, over one operator's historical Claude Code transcripts.

The page describes what an agent's prose looked like before CopyDesk shaped it. It runs `lint()` over assistant content in the JSONL transcripts below `~/.claude/projects/`, counting words after `exclude_markdown()` so code, links, headings, quotations and tables never count as authored prose.

Each stream is measured on its own. **Do not add the rows together.**

No competing tool publishes a measurement like this, which is the reason it is here rather than in a private note.

## The headline

| Stream | Text blocks | Qualifying words | Blocking violations | Violations per 1,000 words |
|---|---:|---:|---:|---:|
| Chat | 15,711 | 1,556,107 | 14,222 | 9.1395 |
| Markdown documents | 2,729 | 626,153 | 3,396 | 5.4236 |

Chat shows roughly two-thirds more violations per word than documents, and chat is the half no file linter ever sees. That gap is the argument for covering chat at all.

## By rule

| Check | Chat | Markdown documents |
|---|---:|---:|
| `announcing-opener` | 1,383 | 0 |
| `banned-word` | 3,501 | 841 |
| `contrast-construction` | 17 | 3 |
| `idiom` | 2 | 2 |
| `long-sentence-rate` | 1,204 | 233 |
| `nested-table` | 7 | 0 |
| `orphan-pointer` | 1,051 | 327 |
| `paragraph-length` | 2,669 | 536 |
| `sentence-length` | 3,717 | 1,446 |
| `soft-offer` | 671 | 8 |

`announcing-opener` fires 1,383 times in chat and never once in a document. An agent announces what it is about to do when it speaks, not when it writes a file.

## Controlling for the sessions that designed the rules

The first run included sessions from 16 and 17 August that designed this work. Those sessions discuss the banned words by name, so they cannot describe ordinary output. A filtered run covers records from `2026-02-01T00:00:00Z` through `2026-08-16T00:00:00+10:00`, exclusive.

The two columns below come from the same command, so they are directly comparable.

| Stream | Measure | Unfiltered | Excluding the design sessions |
|---|---|---:|---:|
| Chat | Text blocks | 15,740 | 15,302 |
| Chat | Qualifying words | 1,497,119 | 1,427,204 |
| Chat | Blocking violations | 14,256 | 13,709 |
| Chat | Violations per 1,000 words | 9.5223 | 9.6055 |
| Markdown documents | Text blocks | 2,741 | 2,492 |
| Markdown documents | Qualifying words | 638,800 | 578,952 |
| Markdown documents | Blocking violations | 3,419 | 3,128 |
| Markdown documents | Violations per 1,000 words | 5.3522 | 5.4029 |

Removing those sessions cuts the raw chat violation count by 547 and **raises** the rate by 0.87 per cent. Documents lose 291 findings and the rate rises 0.95 per cent.

The direction is the informative part. If the design sessions had inflated the baseline, removing them would lower the rate. It does the opposite, so the rules were not measured against prose written by someone thinking about the rules.

## Word-level counts

`count-jargon.py` keeps its own `[a-z']+` tokenizer, so its word counts are reported separately from the denominator above rather than mixed into it.

| Stream | Text blocks | Words | `comprehensive` | `robust` | `delve` | `seam` |
|---|---:|---:|---:|---:|---:|---:|
| Chat | 15,711 | 1,503,465 | 1,119 (7.4 per 10k) | 63 (0.4 per 10k) | 5 (under 0.1) | 79 (0.5 per 10k) |
| Markdown documents | 2,729 | 606,648 | 13 (0.2 per 10k) | 7 (0.1 per 10k) | 0 | 35 (0.6 per 10k) |

Excluding the design sessions:

| Stream | Text blocks | Words | `comprehensive` | `robust` | `delve` | `seam` |
|---|---:|---:|---:|---:|---:|---:|
| Chat | 15,302 | 1,439,462 | 1,118 (7.8 per 10k) | 62 (0.4 per 10k) | 5 (under 0.1) | 77 (0.5 per 10k) |
| Markdown documents | 2,492 | 552,500 | 12 (0.2 per 10k) | 6 (0.1 per 10k) | 0 | 34 (0.6 per 10k) |

The filtered chat count for `comprehensive` falls by one, from 1,119 to 1,118. The ordinary corpus supplies the evidence for that ban, not the sessions that wrote it.

## Reproducing this

The measurement scripts ship with CopyDesk for exactly this reason. Evidence nobody can check is not evidence.

```bash
python3 eval/count-jargon.py ~/.claude/projects --stream chat
python3 eval/count-jargon.py ~/.claude/projects --stream docs
python3 eval/count-jargon.py ~/.claude/projects --stream chat \
  --since 2026-02-01T00:00:00Z --until 2026-08-16T00:00:00+10:00
python3 eval/count-jargon.py ~/.claude/projects --stream docs \
  --since 2026-02-01T00:00:00Z --until 2026-08-16T00:00:00+10:00
```

Numbers from your own transcripts will differ. The method is what transfers.

The linter baseline was computed directly from the same two streams, so every blocking finding, structural checks included, contributes to the per-1,000-word rate.
