# 1. The tool is named CopyDesk

Date: 2026-08-19

## Status

Accepted.

## Context

The tool needed a name it could keep through a language rewrite and a public release. More than 120 candidates were screened.

## Decision

**`copydesk`**, written `CopyDesk` in prose.

A copydesk is the editorial station that reads copy before it goes to print, which is where this tool works.

It was free on npm, PyPI, crates.io, Homebrew, RubyGems, NuGet, Docker Hub and the Go module proxy, with no live product collision, no slang reading and no homophone.

## Casing

Adopted unchanged from CrossRev's [ADR 0010](https://github.com/carlosboeing/crossrev/blob/main/docs/adrs/0010-name-crossrev.md).

`CopyDesk` is the default in every human-readable reference: printed command output, the prose inside help text, documentation, skill text and diagram labels.

Lowercase `copydesk` is an allowlist, not a split down the middle. The complete list: the command and its arguments, the npm package name, config filenames, installed paths, environment-variable prefixes in their lowercase forms, and any string a script matches literally.

The trap that catches people: in help text, `copydesk check README.md` stays lowercase and the sentence above it does not.

`plain-english` is a preset name rather than the tool, so every string naming that preset keeps its own spelling.

## Consequences

**npm accepted `copydesk`.** Published as `copydesk@0.1.0` on 2026-08-19, so the `copydesk-ai` fallback recorded here in advance was never used.

npm's similarity check runs at publish time only. There is no advance query and no appeal, and it had refused the bare name for a sibling project. Deciding a fallback beforehand meant a rejection could not stall the release.

The sibling rejection was re-tested on the same day and is permanent. npm names `cross-env` as the collision. It normalises to `crossenv`, which differs from `crossrev` at two positions, and it is among the most-downloaded packages on the registry.

Nothing on npm comes that close to `copydesk`.

The `-ai` suffix is therefore a workaround for one name rather than a convention. A scoped `@carlosboeing/copydesk` was considered and declined. It buys symmetry with a constraint this project does not have, at the cost of a longer name everywhere.

The installed command is `copydesk` regardless, because the `bin` field decides that rather than the package name.

## The instructive rejections

- **`copylint`** — `copylint.io` is a live product doing brand-rule checks on website copy. Same name, same concept.
- **`strunk`** — has a slang meaning. `blunt` and `bluntly` fail the same screen.
- **`plainvoice`** — `plainvoice.io` is a live on-device dictation product.
- **`housestyle`** — in npm's namespace "style" names CSS, so the package would be misfiled on sight.
- **`lexis`** and the `lex*` neighbourhood — LEXIS is a registered trademark of LexisNexis for text and information retrieval products.
- **Every short real word** — `rubric`, `winnow`, `weir`, `limpid`, `folio`, `pithy`, `taut`, `stark` and eighteen others are taken on both npm and PyPI. That naming era is closed to new entrants.

## The lesson recorded

Registry availability is the weakest signal. Screen in this order: second meanings and homophones, then the live web, then registries, then domains.

Three names reached a recommendation on registry availability alone, and each carried a blocker only a live-web check would find.
