# Releasing

A release is an annotated three-component version tag pushed from the default branch. Everything else is automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## What the workflow does

The tag path has three jobs, in order. A manual workflow run executes only `verify` and cannot publish.

**`verify`** runs the suite on the exact commit and checks the generated instructions still match the preset. It refuses to continue unless `VERSION`, `package.json`, `bin/copydesk --version` and the tag all agree.

It then inspects the tarball. Eleven required files must be present. Evaluation, test, documentation, and compiled bytecode files must be absent. The whole thing must stay under 200 kB.

**`publish`** publishes to npm with [provenance](https://docs.npmjs.com/generating-provenance-statements), then creates a GitHub Release whose body is this version's section of `CHANGELOG.md`.

**`smoke`** installs the published version from the registry, in a clean directory, on the oldest supported interpreter. It checks that `--version` reports the right number, and that a document with findings exits 1. A publish nobody installed is a publish nobody verified.

## Authentication

The workflow prefers **trusted publishing**. It uses OpenID Connect (OIDC), an authentication standard, to mint a short-lived credential scoped to this repository and this workflow. There is no secret to store, rotate or leak.

It cannot be used for a package's first publish. npm requires a package to exist before a trusted publisher can be attached to it, which is [a known limitation](https://github.com/npm/cli/issues/8544).

### First release, once

1. On [npmjs.com](https://www.npmjs.com/), create an **automation** access token. Automation tokens bypass two-factor prompts, which a workflow cannot answer.
2. In this repository, add it under **Settings → Secrets and variables → Actions** as `NPM_TOKEN`.
3. Push the tag. The workflow uses the token.

### Every release after that

1. On npmjs.com, open the package's **Settings → Trusted Publisher**.
2. Choose GitHub Actions, and enter the organisation or user `carlosboeing`, the repository `copydesk`, and the workflow filename `release.yml`. These fields are case-sensitive.
3. **Delete the `NPM_TOKEN` secret.** The workflow reads it only if it exists, so removing it is what makes OIDC the only path.

## Cutting a release

```bash
# 1. Set the version in one place and let the checks catch the rest.
echo "0.10.0" > VERSION
npm version 0.10.0 --no-git-tag-version

# 2. Move the changelog's Unreleased entries under the new heading.
$EDITOR CHANGELOG.md

# 2b. Update the Project status line in README.md to the new version.
#     A test pins it to VERSION, so the suite fails until this is done.
$EDITOR README.md

# 3. Verify the release branch before opening its pull request.
python3 -m unittest discover tests/
python3 scripts/generate-instructions.py --check
npm pack --dry-run --json

# 4. Merge the release pull request with the required squash policy.

# 5. Update a local default branch, verify it again, then create and push the tag.
git switch main
git pull --ff-only origin main
python3 -m unittest discover tests/
git tag -a v0.10.0 -m "CopyDesk 0.10.0"
git push origin v0.10.0
```

The floating `v0` tag is moved by hand, and only for a release people should follow:

```bash
git tag -f -a v0 -m "Floating tag for the 0.x line"
git push -f origin v0
```

## Verifying without publishing

Run the release workflow manually from the Actions tab. The `verify` job runs in full, while `publish` and `smoke` are skipped. Manual dispatch has no publish switch.

The workflow publishes only after a three-component version tag reaches the repository. Do not use a manual run to publish.

## Recovering from a failed tag

Three-component release tags are immutable. If verification or publication fails after a tag is pushed, keep that tag, fix the release on the default branch, and use the next patch version. Confirm the package and GitHub Release state before retrying.

## If npm refuses the name

npm accepted `copydesk` at `0.1.0`, so this does not apply to the current name. It is kept because the check runs at publish time only, with no advance query and no appeal, and a future rename would face it again.

The fallback is **`copydesk-ai`**. A sibling project publishes as `crossrev-ai` for exactly this reason: npm refuses the bare `crossrev` as too similar to `cross-env`.

Change `name` in `package.json` only. The installed command stays `copydesk`, because the `bin` field decides that rather than the package name. Record the refusal in `README.md` and in an ADR rather than hiding it.
