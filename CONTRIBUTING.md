# Contributing to Swarpius

Thanks for considering a contribution. Swarpius is an LLM-driven chat
assistant for controlling a Roon music player. We welcome bug reports, feature
suggestions, documentation improvements, and code contributions.

## Code of Conduct

By participating in this project you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md). Please read it before opening an issue
or PR.

## Reporting

- **Bugs (public)**: open a GitHub issue using the **Bug report** template.
  Include reproduction steps, expected vs actual behaviour, and your
  environment. Scrub private content from any logs you paste — see
  [`SECURITY.md`](SECURITY.md#local-logs-and-privacy) for what to remove.
- **Bugs needing unscrubbed logs**: if a meaningful diagnosis needs
  conversation logs or library titles you'd rather not post publicly,
  email a bundle to **[dev@paraseva.ai](mailto:dev@paraseva.ai)** instead.
  This is a best-effort channel — Swarpius is a solo project with no triage
  SLA, so I can't guarantee any specific report gets investigated, but private
  content stays out of the public tracker.
- **Feature requests**: open a GitHub issue using the **Feature request**
  template. Describe the use case before the proposed implementation.
- **Security vulnerabilities**: *do not* open a public issue. See
  [`SECURITY.md`](SECURITY.md) for the private disclosure channels.

## Setting up your dev environment

Swarpius is a monorepo with a Python agent, a React/Vite web client, and a
couple of optional Docker services. Per-component setup lives in each
sub-project's README:

- [`agent/README.md`](agent/README.md): Python 3.13, requirements files,
  and the optional `./dev` wrapper for WSL.
- [`web-client/README.md`](web-client/README.md): Node 22, npm.
- [`README.md`](README.md) in the root folder: overall quickstart and Docker Compose.

On Windows, develop inside WSL on the WSL filesystem rather than a
Windows-mounted drive (`/mnt/c/...`), which causes subtle pytest tmp-file
issues. Native Linux and macOS need no special handling.

## Branching convention

Branch from `main`. Branch names must match this pattern (enforced by a
required CI check):

```
^(feature|fix|docs|chore|refactor)/[a-z0-9._-]+$
```

| Prefix | When to use |
|---|---|
| `feature/` | New functionality |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `chore/` | Housekeeping, build, dependencies |
| `refactor/` | Non-functional code restructuring |

Examples: `feature/per-zone-history`, `fix/parse-edge-case`, `chore/dep-bumps`,
`docs/contributing-md`, `refactor/runtime-state-split`.

(Dependabot's `dependabot/...` branches are exempt automatically.)

## Making changes

### Code style

The repo follows the criteria in [`docs/coding-standards.md`](docs/coding-standards.md). PR review uses them as lenses — your contribution doesn't need to be perfect on every one, but if a reviewer cites a criterion, that's the framing. Particular weight on:

- **Tests exercise real logic, not pre-configured fakes.** Stub at the API boundary; inherit / use real production code above it.
- **Comments are necessary, concise, and history-free.** Default to none. Explain non-obvious *why* in a short line. Bug numbers, dates, and "this used to do X" belong in the PR description, not in code.
- **No dead code or speculative abstraction.** Three similar lines beat a premature abstraction; remove unused branches and imports.

See the doc for the full list and anchoring examples.

### Lint and test before opening a PR

CI will run these on every PR; running locally first saves a round-trip.

**Agent** (from `agent/`, with the venv activated):

```bash
ruff check .
pytest
```

(WSL contributors can use the `./dev` wrapper instead — `./dev ruff check .`, `./dev pytest` — which activates the WSL-specific venv and sets a WSL-side tmp dir; see [`agent/README.md`](agent/README.md).)

**Web client** (from `web-client/`):

```bash
npm run lint
npm test
npm run build
```

The `build` step catches TypeScript errors that the dev-server hot-reload
ignores.

### Pre-commit hooks (recommended)

We use [pre-commit](https://pre-commit.com/) for `detect-secrets` and a few
hygiene checks. Install once after cloning:

```bash
pip install pre-commit
pre-commit install
```

The hooks then run automatically on every `git commit`. Run them manually
with:

```bash
pre-commit run --all-files
```

### Sign off your commits (DCO)

We use the
[Developer Certificate of Origin](https://developercertificate.org/) to track
contribution provenance. Every commit must include a `Signed-off-by:` line
attesting that you have the right to submit the work under the project's
licence.

The easy way: pass `-s` when committing.

```bash
git commit -s -m "feat: add per-zone play history"
```

This appends a line like `Signed-off-by: Your Name <your.email@example.com>`
using your `git config user.name` and `user.email`. A `DCO` check will fail
on any PR with unsigned commits.

To retroactively add sign-offs to commits you forgot to sign:

```bash
git rebase --signoff main      # adds sign-off to every commit since main
git push --force-with-lease    # safely update the remote
```

## Opening a pull request

1. Push your branch and open a PR against `main`.
2. Fill in the PR template, including a brief test plan.
3. Make sure all required checks pass — `Agent (Python)`, `Web client (Node)`,
   `Branch name`, and `DCO`.
4. Resolve any conversation threads before requesting merge.
5. Once approved, the PR will be **squash-merged** into `main`. We don't use
   merge or rebase merges, so feel free to keep your feature-branch commit
   history messy ("WIP", "fix typo", etc.) — it'll be collapsed into a single
   commit on merge.

After merge, your feature branch is auto-deleted from the remote.

## Scope of PRs

Smaller is better. A PR that does one thing well is easier to review, easier
to revert if needed, and easier to bisect later. If a change is growing
beyond ~500 lines or touching unrelated areas, consider splitting it.

## Releasing

(Maintainer-facing — included here so contributors know how their merged
work gets released.)

Swarpius uses [Semantic Versioning](https://semver.org/) with **one version
across the whole monorepo**. Releases are cut manually.

**Version-bump rules:**

- **PATCH** (`1.0.X`): bug fixes only, no behaviour change for users
  beyond the fix.
- **MINOR** (`1.X.0`): additive features, no breaking changes.
- **MAJOR** (`X.0.0`): breaking changes (env vars renamed, removed
  capabilities, WS protocol changes, etc.).

**The version lives in one place — `agent/VERSION`.** Everything derives from it: the agent's Roon-reported version, the web client's displayed version and update check, and the installer metadata (Windows `version_info`, macOS `CFBundle*`, the Inno installer). The only mirror is `web-client/package.json`'s `"version"` field — npm requires its own, and the web client reads its displayed version from it at build time. It must equal `agent/VERSION`; a unit test (`agent/tests/test_version_sync.py`, run in normal CI) and the installer build both fail if they disagree.

**Release checklist** (follow top to bottom):

1. **Decide the version** `X.Y.Z` (per the rules above).
2. **Set the version** in the two source files — and only these two:
   - [ ] `agent/VERSION` → `X.Y.Z`
   - [ ] `web-client/package.json` → `"version": "X.Y.Z"` (keep equal to `agent/VERSION`)
3. **Add a `CHANGELOG.md` entry** in [Keep a Changelog](https://keepachangelog.com/) format (`### Added` / `### Changed` / `### Fixed` / `### Removed`).
4. **Open a `chore/release-vX.Y.Z` PR** with the two version bumps + the CHANGELOG entry; merge once green (CI's version-consistency check guards step 2).
5. **Tag the merge commit on `main`** — the tag must match `agent/VERSION`:
   ```bash
   git checkout main && git pull
   git tag vX.Y.Z          # X.Y.Z == agent/VERSION
   git push origin vX.Y.Z
   ```
6. **Build the installers**: run the **Installer** workflow (`.github/workflows/installer.yml`). It produces the Linux AppImage, macOS `.dmg`, and Windows `Swarpius-Setup.exe`, all stamped from `agent/VERSION`.
7. **Cut the GitHub Release** from the tag (`gh release create vX.Y.Z` or the web UI), with the CHANGELOG entry as the notes, and attach the three bundles as **direct release assets** (the raw files — not the workflow's artifact zips, which wrap the files and break the download-time security checks).

> The release workflow signs the artefacts — macOS notarised, Windows code-signed, and all three installers GPG-signed (`SHA256SUMS.asc`). Tag-triggered builds and automatic asset upload are a planned convenience; until they land, steps 6–7 are run by hand.

## Licence

By contributing, you agree that your contributions will be licensed under the
project's [LICENSE](LICENSE) (Apache 2.0).
