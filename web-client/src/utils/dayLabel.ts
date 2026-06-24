function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** Stable local-day key (YYYY-MM-DD) for a timestamp. Used to disambiguate
 *  request ids, which repeat across days as conversation ids reset daily. */
export function dayKey(timestamp: number): string {
  const d = new Date(timestamp)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
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
