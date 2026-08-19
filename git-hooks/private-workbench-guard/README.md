# private-workbench-guard

A git `pre-commit` hook for the **public-repo-plus-private-workbench** pattern: an independent private clone nested inside a public repository, sharing one working tree.

Instances today: `carlosboeing/crossrev` with `crossrev-workbench`, and `carlosboeing/penmark` with `penmark-workbench`. Both nest the private clone at `.workbench/`.

## The pattern

Two repositories, one working tree. The public repository holds code, user docs, ADRs, roadmap and changelog. The private one holds the build process — brainstorms, discovery, designs, plans, reviews, scratch notes — plus anything about brand, company or commercial direction.

```
~/Projects/carlos/crossrev/          public  (carlosboeing/crossrev)
  .workbench/                        private (carlosboeing/crossrev-workbench), gitignored above
```

`git …` at the root targets the public repository. `git -C .workbench …` targets the private one. They never cross-commit.

## What already protects it, without this hook

Worth knowing, because it decides what the hook is actually for. Three layers exist before any hook runs, and none of them needs vigilance:

1. **`.workbench/` is gitignored** in the public repository, so `git add -A` at the root cannot sweep workbench files into a public commit.
2. **Git will not reach inside a nested repository.** Even `git add -f .workbench/some/file` stages nothing — it silently no-ops. A workbench *file* cannot become a public blob by accident.
3. **Paths outside the work tree are refused.** From inside `.workbench/`, `git add ../scripts/lint.sh` fails outright.

So the file-level leak is already closed. What remains is the content-level one.

## What the hook checks

**1. The workbench staged as a gitlink.** `git add -f .workbench` does succeed, recording a submodule reference. Clones get none of the content, but the private repository's name and commit SHA become public. The trailing `(/|$)` in the match matters — the staged path has no slash, and requiring one misses the only case that can actually happen.

**2. Workbench vocabulary in added lines.** The scan list from the repository's `CLAUDE.md`: `.workbench` paths, hosted service or tier, monetisation, pricing, per-seat figures, dollar amounts.

The second check is the reason this hook exists. Private content retyped, pasted or summarised into a public file — from the correct directory, with a correct path — is invisible to every structural layer above. Nothing about the path is wrong. Only the words are.

Both checks refuse the commit. `git commit --no-verify` is the deliberate override.

## Why the money pattern is not `\$[0-9]`

That is what `CLAUDE.md` documents for the manual sweep, where a human reads the hits. As a blocking hook it is unusable: `\$[0-9]` matches every shell positional parameter.

Measured against `crossrev`, over its last 40 commits:

| Term | Commits blocked |
|---|---|
| `\$[0-9]` | **16 of 40** |
| `\.workbench` | 1 |
| `hosted (service\|tier)` | 1 |
| `monetiz\|monetis` | 1 |
| `pricing` | 1 |
| `per (seat\|month\|user)` | 0 |

Requiring two digits or a separator — `\$[0-9]{2,}` or `\$[0-9]+[.,][0-9]` — still catches `$50`, `$1.50` and `$4,500` while ignoring `"$1"`. With that change plus the allowlist below, **0 of the same 40 commits are blocked**, and a synthetic leak carrying all three vocabulary classes is still caught.

The general point is worth keeping: a hook that fires on 40% of legitimate commits gets bypassed reflexively, and a reflexively bypassed hook is worse than none.

## The allowlist

`CLAUDE.md`, `AGENTS.md`, `.gitignore`, and the hook script itself are skipped. Stating a rule requires naming the words the rule forbids, so these files can never satisfy their own check. `CLAUDE.md` documents the same exclusion for the manual sweep, where a human reads them instead.

## What this deliberately does not do

Recorded so it does not get rebuilt. An earlier design guarded the *working directory* — blocking bare `git commit` and `gh` writes when the shell had drifted into the nested repository. It was dropped, for two reasons that hold generally:

**A wrong-repo commit is recoverable and cannot leak.** Committing from inside `.workbench` puts the content in the private repository, which is where it belongs. You get a misleading commit message, fixable with `git reset`. Nothing escapes.

**A directory guard points the wrong way.** The leak direction is private words reaching a *public* repository, which happens from the correct directory with the correct repo named. A cwd rule blocks the harmless direction and permits the harmful one. No routing rule can see whether the words are private — that is a content question, which is why the check that survived is a content check.

## Install

Per repository, once. The script is versioned inside the target repository rather than sourced from here, so a fresh clone carries it.

```bash
REPO=~/Projects/carlos/crossrev
mkdir -p "$REPO/scripts/githooks"
cp pre-commit "$REPO/scripts/githooks/pre-commit"
chmod +x "$REPO/scripts/githooks/pre-commit"
git -C "$REPO" config core.hooksPath scripts/githooks
```

`core.hooksPath` is per clone and git will not enable it automatically, so the config line is repeated on every machine. That is git's decision, not a gap here — a hook that ran on clone would be a remote code execution vector.

Verify it is live:

```bash
git -C "$REPO" config --get core.hooksPath      # scripts/githooks
git -C "$REPO" add -f .workbench && git -C "$REPO" commit -m probe
# expect: refusing — the private workbench is staged
git -C "$REPO" reset
```

## Adding a third instance

Copy the script, run the two commands above, and add the repository to the list at the top of this file. The hook hardcodes `.workbench` as the nested directory name; a pair using a different name needs that string changed in both the gitlink check and the vocabulary pattern.
