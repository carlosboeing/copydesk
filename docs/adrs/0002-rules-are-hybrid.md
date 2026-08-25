# 2. Rules are a hybrid under a uniform contract

Date: 2026-08-19

## Status

Accepted.

## Context

Making every rule data would force heuristics into token lists that cannot express them. Making every rule code would put the part users most want to change behind a release.

## Decision

Pattern rules become data. Metric and structural rules stay code with their parameters declared. **The config surface treats both identically.**

| Group | Home | Reason |
|---|---|---|
| Pattern and phrase | data, in `patterns` | every user wants to differ here |
| Metric | code, parameters exposed | thresholds and word lists vary, algorithms do not |
| Structural | code, toggle only | Markdown structure analysis has nothing to configure |

A user writes `"sentence-length": { "severity": "warn", "max": 30 }` and `"banned-word": { "add": ["synergy"] }` in the same `rules` block, and never needs to know which is which.

## Consequences

Data contains most of the rule set, which is where the split belongs.

Pattern data keeps the form of Vale's `existence` and `substitution` checks, so an importer stays a translation rather than a redesign. Honouring that form costs nothing today and is what keeps the option open.

`unglossed-term` is recorded under `rules` and never under `patterns`, even though it has a word list. Its first-use detection, gloss detection and skip rules are algorithm. Recording it as a token list would mislead a later port into treating a heuristic as data.

Which rules are compiled and which are data is deliberately outside the compatibility contract, so the line can move without a breaking change.
