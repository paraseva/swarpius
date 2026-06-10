const KEY = 'swarpius.bundleMode'

/**
 * Remember the run mode so a cold load after the agent has exited (the bundle
 * navigate-away case) can still tailor the unreachable message. Rewritten on
 * every update, so it self-corrects across run modes.
 */
export function rememberBundleMode(isBundle: boolean): void {
  try {
    if (isBundle) {
      window.localStorage.setItem(KEY, '1')
    } else {
      window.localStorage.removeItem(KEY)
    }
  } catch {
    /* localStorage unavailable (e.g. private mode) — best-effort only */
  }
}

/** True if the last known session was the desktop bundle. */
export function wasBundleMode(): boolean {
  try {
    return window.localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}
