<!-- Keep pull requests small and focused. The body is read by people who have never seen the branch. -->

## Summary

<!--
What changes, and why, in two or three sentences before any detail.
Cite files in this repository by path. Cite nothing outside it — no private
repository, no path on your own machine. Restate the reasoning here rather
than pointing at where it was written down.
-->

## Related issue

<!-- Closes #NNN, or "n/a" for a standalone change. -->

## Out of scope

<!-- What this deliberately does not do, and the issue tracking it. "n/a" is fine. -->

## How was this tested?

<!-- Paste the output. Do not assert that it passed. -->

- [ ] `python3 -m unittest discover tests/` — test count and result pasted above
- [ ] CI green, with the check count

## Checklist

- [ ] Conventional Commit subject (`type(scope): description`, imperative, <= 72 chars)
- [ ] Nothing in the body names a private repository, a workbench path, or a path on a personal machine
- [ ] `CopyDesk` in anything a person reads; lowercase `copydesk` only in the closed list (`CLAUDE.md`)
- [ ] No frozen contract item moved, or the break is justified above
- [ ] No dependency outside the Python standard library on the linter path
- [ ] `CHANGELOG.md` / `docs/ROADMAP.md` / docs / ADR updated in the same commit set if user-facing behaviour changed
