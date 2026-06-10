/**
 * Pick sparsely-spaced x-axis date labels (~6 visible) for a trend chart,
 * always anchoring the last point so the current date is always labelled.
 *
 * Callers guard with `points.length >= 2` — the function isn't defined
 * for empty input.
 */
export function computeDateLabels(
  points: readonly { date: string }[],
): { idx: number; label: string }[] {
  const interval = Math.max(1, Math.floor(points.length / 6))
  const labels: { idx: number; label: string }[] = []
  for (let i = 0; i < points.length; i += interval) {
    labels.push({ idx: i, label: points[i].date.slice(5) })
  }
  if (labels.length === 0 || labels[labels.length - 1].idx !== points.length - 1) {
    labels.push({
      idx: points.length - 1,
      label: points[points.length - 1].date.slice(5),
    })
  }
  return labels
}
