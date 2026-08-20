# 3. Config compiles to instructions

Date: 2026-08-20

## Status

Accepted.

## Context

Preventing prose flaws requires placing instructions directly into model context before generation. Different coding harnesses consume instructions through different files. Claude Code reads output styles and prompt reminders. Other harnesses read instruction blocks in repository documentation.

Maintaining separate hand-written instruction files per harness leads to drift and inconsistent rule application.

## Decision

**Treat prevention as a compilation pipeline.**

Configuration files and style presets serve as the single source of truth. Instruction sets, output styles, and prompt reminders are generated artifacts compiled by `scripts/generate-instructions.py` and the setup wizard.

No instruction file is hand-edited. Every generated static file embeds a build fingerprint derived from compiled output. If inputs or source configs change, `copydesk doctor` and reminder hooks detect stale fingerprints and request a rebuild.

## Consequences

- Configuration changes immediately propagate across target harnesses and channels through deterministic compilation.
- Static output styles remain fast to load with zero runtime rendering overhead on turn startup.
- Runtime drift between installed files and configuration is reliably detectable via fingerprints.
- Adding support for future harnesses requires writing a compiler target rather than creating manual copies.

## Alternatives considered

1. **Hand-written per-harness files**: Rejected because hand-maintained copies diverge over time.
2. **Runtime template rendering on every turn**: Rejected because dynamic template expansion on prompt submission introduces latency.
