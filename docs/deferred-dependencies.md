# Deferred Dependencies

This is the index of dependency major-version bumps that Dependabot is
configured to ignore in `.github/dependabot.yml`. Each entry exists
because the bump is unsafe to take alone — typically the package's
peer or transitive dependencies haven't aligned with the new major
yet, and merging would land a broken toolchain (install fails, lint
stack stops working, tests no longer run, etc.).

Treat this list as a maintenance debt log:

- **Review cadence:** quarterly (or whenever a related ecosystem
  major lands), update the "last reviewed" date below each time.
- **Lifting an ignore:** remove the `ignore` entry in
  `.github/dependabot.yml`, then either let the next Dependabot run
  propose the bump or merge it manually. Update this file to reflect
  what changed.
- **Adding to the list:** mirror the format below — package, pinned
  major line, reason it can't move, what needs to change before the
  ignore can be lifted.

## Active deferrals

### `typescript`

- **Pinned at:** `5.x` (current major)
- **Why deferred:** `typescript-eslint` declares a peer dependency of
  `typescript: <6.0.0`, so a TS major bump lands a lint stack that
  npm silently installs in a broken state.
- **Lift condition:** `typescript-eslint` provides a release with peer
  dep `typescript: ^6.0` (or removes the upper bound).
- **Watch:** <https://github.com/typescript-eslint/typescript-eslint/releases>
- **Last reviewed:** 2026-05-17

### `eslint`

- **Pinned at:** `9.x`
- **Why deferred:** `typescript-eslint@8.x` peers on
  `eslint ^8.57 || ^9.0`, so jumping to ESLint 10 would break the
  lint stack until `typescript-eslint` provides ESLint 10 support.
  Dependabot's compatibility badge for `eslint` 9→10 was 7% at time
  of deferral — strong signal of widespread ecosystem breakage.
- **Lift condition:** Coordinated upgrade: `typescript-eslint` provides
  ESLint 10 support, `@eslint/js` has matched, and lint plugins
  (`eslint-plugin-react-hooks`, etc.) declare ESLint 10 peer support.
- **Watch:** <https://github.com/typescript-eslint/typescript-eslint/releases>
- **Last reviewed:** 2026-05-17

### `@eslint/js`

- **Pinned at:** `9.x`
- **Why deferred:** `@eslint/js@10` declares `eslint: ^10.0.0` as a
  (peerOptional) peer dependency. Bumping it without bumping `eslint`
  fails `npm ci`. Bumping both together requires the wider lint stack
  to support ESLint 10 (see `eslint` entry above).
- **Lift condition:** lifted together with the `eslint` ignore.
- **Last reviewed:** 2026-05-17

### `@vitejs/plugin-react`

- **Pinned at:** `5.x`
- **Why deferred:** v6 removed/renamed the `babel` option, breaking
  the existing React Compiler integration in `vite.config.ts` (we
  pass `babel: { plugins: [['babel-plugin-react-compiler']] }`).
  Migrating to the v6 entrypoint is a small but real config change.
- **Lift condition:** when ready to migrate, read the v6 release
  notes for the new React Compiler integration pattern, update
  `vite.config.ts`, then lift this ignore.
- **Watch:** <https://github.com/vitejs/vite-plugin-react/releases>
- **Last reviewed:** 2026-05-17

### `vitest`

- **Pinned at:** `4.x` (major bumps deferred)
- **Why deferred:** Vitest major bumps carry breaking schema/runner
  changes — the 3→4 migration needed test-config/setup changes, and the
  jsdom + Node 22 test environment is sensitive to them. Majors are taken
  deliberately, not auto-merged. We are on 4.x; the ignore now guards the
  next major (4→5).
- **Lift condition:** when a new Vitest major lands, the ecosystem (Vite,
  plugins) has caught up, and we're ready to migrate the test config.
- **Watch:** <https://github.com/vitest-dev/vitest/releases>
- **Last reviewed:** 2026-06-07

### `python` (Docker base image, `/agent`)

- **Pinned at:** `3.13-slim-bookworm`
- **Why deferred:** Dependabot bumped the agent Dockerfile from
  `python:3.13` to `python:3.14`, which broke the build because
  `litellm==1.85.0` declares
  `Requires-Python >=3.10,<3.14`. At deferral time even
  `litellm==1.86.0rc1` still capped at `<3.14`, so no release line
  was viable on 3.14. CI (`.github/workflows/ci.yml`) and
  `CLAUDE.md` both target 3.13, so the Docker bump was an outlier.
  Note: Dependabot treats Python `3.13 → 3.14` as a *minor* semver
  bump (the existing wildcard ignore only catches majors), so the
  ignore is scoped to `version-update:semver-minor` on
  `dependency-name: python` — patch bumps (e.g. `3.13.1 → 3.13.2`)
  still flow through.
- **Lift condition:** `litellm` provides a release that allows Python
  `>=3.14` (check the latest release's `Requires-Python` on PyPI).
  When lifted, bump litellm in `agent/requirements-server.txt` and
  the Dockerfile together.
- **Watch:** <https://github.com/BerriAI/litellm/releases> and
  <https://pypi.org/project/litellm/#history> (look at
  `Requires-Python`).
- **Last reviewed:** 2026-05-17

## Lifted deferrals

When an ignore is lifted, move its entry here with a note on what
changed and the date. Don't delete — the historical record helps
when the same pattern recurs.
