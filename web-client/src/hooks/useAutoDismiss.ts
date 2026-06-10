import React from 'react'

/**
 * Clear a transient message after it's been shown for `ms`. Calls
 * `onExpire` once `active` has held true for the delay; the timer is
 * cancelled (and restarted) whenever `active` toggles, so a stale
 * message can't linger and mislead later.
 */
export function useAutoDismiss(
  active: boolean,
  onExpire: () => void,
  ms = 5000,
): void {
  React.useEffect(() => {
    if (!active) return
    const timer = setTimeout(onExpire, ms)
    return () => clearTimeout(timer)
  }, [active, onExpire, ms])
}
