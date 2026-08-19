# 1. The tool is named CopyDesk

Date: 2026-08-19

## Status

Accepted.

## Context

The tool needed a name it could keep through a language rewrite and a public release. More than 120 candidates were screened.

## Decision

**`copydesk`**, written `CopyDesk` in prose.

A copydesk is the editorial station that reads copy before it goes to print, which is where this tool sits.

It was free on npm, PyPI, crates.io, Homebrew, RubyGems, NuGet, Docker Hub and the Go module proxy, with no live product collision, no slang reading and no homophone.

## Casing

Adopted unchanged from CrossRev's [ADR 0010](https://github.com/carlosboeing/crossrev/blob/main/docs/adrs/0010-name-crossrev.md).

`CopyDesk` is the default in every human-readable reference: printed command output, the prose inside help text, documentation, skill text and diagram labels.

Lowercase `copydesk` is an allowlist, not a split down the middle. The complete list: the command and its arguments, the npm package name, config filenames, installed paths, environment-variable prefixes in their lowercase forms, and any string a script matches literally.

The trap that catches people: in help text, `copydesk check README.md` stays lowercase and the sentence above it does not.

`plain-english` is a preset name rather than the tool, so every string naming that preset keeps its own spelling.

## Consequences

The npm package may need to be `copydesk-ai`. npm's similarity check runs only at publish time, and it refused `crossrev` with no appeal. The installed command stays `copydesk` either way, because `bin` decides that rather than the package name.

## The instructive rejections

- **`copylint`** — `copylint.io` is a live product doing brand-rule checks on website copy. Same name, same concept.
- **`strunk`** — carries a slang meaning. `blunt` and `bluntly` fail the same screen.
- **`plainvoice`** — `plainvoice.io` is a live on-device dictation product.
- **`housestyle`** — in npm's namespace "style" reads as CSS, so the package would be misfiled on sight.
- **`lexis`** and the `lex*` neighbourhood — LEXIS is a registered trademark of LexisNexis for text and information retrieval products.
- **Every short real word** — `rubric`, `winnow`, `weir`, `limpid`, `folio`, `pithy`, `taut`, `stark` and eighteen others are taken on both npm and PyPI. That naming era is closed to new entrants.

## The lesson recorded

Registry availability is the weakest signal. Screen in this order: second meanings and homophones, then the live web, then registries, then domains.

Three names reached a recommendation on registry availability alone, and each carried a blocker only a live-web check would find.
