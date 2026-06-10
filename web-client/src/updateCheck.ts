import { useCallback, useEffect, useState } from 'react'

/** Release downloads page — where the "Update" button sends the user. */
export const UPDATE_RELEASES_URL =
  'https://github.com/paraseva/swarpius/releases/latest'

const RELEASES_API =
  'https://api.github.com/repos/paraseva/swarpius/releases/latest'
const CACHE_KEY = 'swarpius:update-check'
const CACHE_TTL_MS = 6 * 60 * 60 * 1000 // 6h — well within GitHub's anon rate limit
const FETCH_TIMEOUT_MS = 8000 // bound a hung request so "Checking…" can't stick

export function parseSemver(v: string): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(v.trim())
  if (!m) return null
  return [Number(m[1]), Number(m[2]), Number(m[3])]
}

/** True iff ``latest`` is a strictly higher semver than ``current``.
 *  Returns false if either is unparseable, so a malformed release tag
 *  never surfaces a phantom update. */
export function isNewerVersion(current: string, latest: string): boolean {
  const c = parseSemver(current)
  const l = parseSemver(latest)
  if (!c || !l) return false
  for (let i = 0; i < 3; i += 1) {
    if (l[i] > c[i]) return true
    if (l[i] < c[i]) return false
  }
  return false
}

interface CacheEntry {
  latest: string
  ts: number
}

function readCache(now: number): string | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const entry = JSON.parse(raw) as CacheEntry
    if (typeof entry.latest !== 'string' || typeof entry.ts !== 'number') return null
    return now - entry.ts < CACHE_TTL_MS ? entry.latest : null
  } catch {
    return null
  }
}

async function fetchLatestTag(): Promise<string | null> {
  // Time-box the request: a clean 404 resolves on its own, but a blocked host
  // would hang forever without this.
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)
  try {
    const res = await fetch(RELEASES_API, {
      headers: { Accept: 'application/vnd.github+json' },
      signal: controller.signal,
    })
    if (!res.ok) return null // 404 until the public repo has a release, rate-limit, etc.
    const data = (await res.json()) as { tag_name?: string }
    return typeof data.tag_name === 'string' ? data.tag_name : null
  } finally {
    clearTimeout(timer)
  }
}

// Fetch + cache the latest tag, reporting it via onResult. Swallows every
// failure — an update check must never raise.
function fetchAndStore(onResult: (tag: string) => void): Promise<void> {
  return fetchLatestTag()
    .then((tag) => {
      if (!tag) return
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({ latest: tag, ts: Date.now() }))
      } catch {
        /* storage full / disabled — the check still works, just uncached */
      }
      onResult(tag)
    })
    .catch(() => {})
}

export interface UpdateCheckState {
  /** Latest release tag, but only when it's newer than the current version. */
  available: string | null
  /** A network check is currently in flight. */
  checking: boolean
  /** Re-check now, bypassing the cache. Runs even when auto-check is off. */
  checkNow: () => void
}

/**
 * Update-availability state plus a manual re-check trigger. `available` holds
 * the latest release tag only when it's newer than `currentVersion`. Results
 * are cached in localStorage for a few hours so repeat visits don't re-hit
 * GitHub; `checkNow` ignores that cache. Any failure (network, 404, rate-limit,
 * malformed response) surfaces nothing.
 */
export function useUpdateCheck(enabled: boolean, currentVersion: string): UpdateCheckState {
  const [latest, setLatest] = useState<string | null>(() => readCache(Date.now()))
  const [checking, setChecking] = useState(false)

  const checkNow = useCallback(() => {
    setChecking(true)
    fetchAndStore(setLatest).finally(() => setChecking(false))
  }, [])

  // Auto-check once on mount when enabled and nothing fresh is cached. A
  // background fetch (not checkNow) so no state is set synchronously in the
  // effect; checkNow owns the visible checking state.
  useEffect(() => {
    if (!enabled || latest) return
    void fetchAndStore(setLatest)
  }, [enabled, latest])

  const available = latest && isNewerVersion(currentVersion, latest) ? latest : null
  return { available, checking, checkNow }
}

const UPDATE_CHECK_ENABLED_KEY = 'swarpius:update-check-enabled'

/**
 * The "check for updates automatically" preference. Purely client-side (the
 * check runs in the browser), so it lives in localStorage and defaults to on
 * (opt-out) — no backend round-trip.
 */
export function useUpdateCheckEnabled(): {
  enabled: boolean
  setEnabled: (value: boolean) => void
} {
  const [enabled, setEnabledState] = useState<boolean>(() => {
    try {
      return localStorage.getItem(UPDATE_CHECK_ENABLED_KEY) !== 'false'
    } catch {
      return true
    }
  })

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value)
    try {
      localStorage.setItem(UPDATE_CHECK_ENABLED_KEY, value ? 'true' : 'false')
    } catch {
      /* storage disabled — the toggle still works for this session */
    }
  }, [])

  return { enabled, setEnabled }
}
