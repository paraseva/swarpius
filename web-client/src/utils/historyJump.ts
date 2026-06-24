/** Load every day from `dayStartMs` up to the earliest history currently in
 *  memory, keeping loaded history contiguous — fill the gap, don't punch a hole.
 *  Returns true when a range load was issued (a batch is incoming, so the caller
 *  should wait for it before scrolling), false when the day is already within the
 *  loaded range and the caller can act immediately.
 *
 *  Shared by the date picker and the request-id badge sync so both jump the same
 *  way. */
export function loadDaysThrough(
  dayStartMs: number,
  messages: readonly { timestamp: number }[],
  requestHistoryRange: ((startMs: number, endMs: number) => void) | undefined,
): boolean {
  const oldestLoaded = messages.length > 0 ? messages[0].timestamp : Date.now()
  if (dayStartMs >= oldestLoaded) return false
  requestHistoryRange?.(dayStartMs, oldestLoaded)
  return true
}
