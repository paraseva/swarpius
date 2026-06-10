/**
 * Git ref badge gating.
 *
 * `git_ref` is captured by the analyser at scan time via `git rev-parse`.
 * Installer-bundled agents (no `.git` in cwd) yield no ref, and a legacy
 * code path wrote the literal string `"unknown"` for those cases — both
 * states should hide the badge rather than render a meaningless slice.
 */
export function isDisplayableGitRef(
  ref: string | null | undefined,
): ref is string {
  if (!ref) return false
  return ref.trim().toLowerCase() !== 'unknown'
}
