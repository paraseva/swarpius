// Re-export from shared utility for backward compatibility.
export { parseJson } from '../utils/parseJson'

export const imageCacheKey = (imageKey: string, width: number, height: number): string =>
  `${imageKey}:${width}:${height}`

/**
 * Format a zone display name consistently: "Alias (Actual)" if alias exists,
 * otherwise just "Actual". For groups with a group name: "Group Name (Actual)".
 */
export const formatZoneLabel = (
  displayName: string,
  alias?: string | null,
  groupName?: string | null,
): string => {
  if (groupName) return `${groupName} (${displayName})`
  if (alias) return `${alias} (${displayName})`
  return displayName
}

export const formatDuration = (seconds: number): string => {
  const safe = Math.max(0, Math.floor(seconds))
  const mins = Math.floor(safe / 60)
  const secs = safe % 60
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

export const formatVolumePercent = (
  value: number,
  min: number = 0,
  max: number = 100,
): string => {
  const range = max - min
  if (!isFinite(range) || range <= 0) {
    return `${Math.round(value)}%`
  }
  const pct = ((value - min) / range) * 100
  const clamped = Math.max(0, Math.min(100, pct))
  return `${Math.round(clamped)}%`
}
