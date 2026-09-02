# Contributing to CopyDesk

Thanks for looking. CopyDesk is early, so the most useful contributions are bug reports with a reproducing document and disagreements with a rule.

## Running the suite

```bash
python3 -m unittest discover tests/
```

**The suite needs Python 3.11 or later**, because `eval/` uses `tomllib`. The shipped linter itself runs on 3.9, and a separate continuous-integration job proves that on every push.

No dependencies beyond the Python standard library. The gate runs on every Write and Edit in a hooked harness, so a third-party import on that path is a latency and install-friction cost the project will not take.

## The pre-commit hook

```bash
ln -sf ../../git-hooks/pre-commit .git/hooks/pre-commit
```

It runs the suite and has no path guard, deliberately. An earlier version guarded on a bundle path and exited 0 in silence once that path moved.

## Proposing a rule change

Rules split three ways, and where a rule lives decides how you change it.

| Group | Home | How to change it |
|---|---|---|
| Pattern and phrase | `rules/<preset>.json`, under `patterns` | edit the preset, or your own config |
| Metric | code, with parameters exposed | edit the threshold in `rules`; the algorithm is code |
| Structural | code, toggle only | `"severity": "off"` in your config |

If a rule fires on prose you believe is fine, open an issue with the sentence. A rule that costs more than it catches is a defect.

## What is frozen

`CLAUDE.md` lists six frozen items, including rule identifiers and the command-line surface. A pull request changing one of them needs to say why the break is worth it.

## Pull requests and issues

Open a pull request with [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md). Filing with `gh pr create --body` skips the template, so apply it by hand.

A body is read by people who have never seen the branch. Open with what changes and why. Cite files in this repository by path. Paste the test output rather than asserting that it passed.

An issue title is one clause under 60 characters naming the change or the symptom. A body says what was measured and cites `file:line` for code that already exists.

Bug reports and feature requests use the structured forms in the issue tracker. Use [SUPPORT.md](SUPPORT.md) for questions that do not need a code change.

## Continuous integration

Every push and pull request runs the suite, generated-instruction check, shellcheck, documentation self-check, shipped-surface matrix, and npm packaging matrix on Node 18 and 24. The `required` check is the stable aggregate for those jobs and must keep that name.

Workflow actions are pinned to full commit SHAs. Dependabot checks those pins each week. Do not replace a SHA with a branch or version tag.

## Commits

Conventional Commits: `<type>(<scope>): <description>`, imperative mood, subject at most 72 characters. The body explains why.

## Releasing

A release is an annotated three-component version tag pushed from the default branch. Manual release workflow runs verify the package without publishing. See [docs/releasing.md](docs/releasing.md).
