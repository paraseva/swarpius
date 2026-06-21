function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** Human label for the calendar day a timestamp falls on, relative to `now`:
 *  "Today", "Yesterday", else e.g. "Mon 16 Jun". */
export function dayLabel(timestamp: number, now: number = Date.now()): string {
  const day = startOfLocalDay(new Date(timestamp))
  const diffDays = Math.round((startOfLocalDay(new Date(now)) - day) / 86_400_000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  return new Date(timestamp).toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short',
  })
}

/** True when two timestamps fall on different local calendar days. */
export function isNewDay(a: number, b: number): boolean {
  return startOfLocalDay(new Date(a)) !== startOfLocalDay(new Date(b))
}
