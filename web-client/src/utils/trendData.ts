/**
 * Compute daily breakdowns and rolling-window trend data from metrics entries.
 */
import { isDisplayableGitRef } from './gitRef'

export interface MetricsEntryInput {
  date: string
  finding_count: number
  findings_by_severity: Record<string, number>
  requests: number
  git_ref: string
}

export interface DailyBucket {
  date: string
  conversations: number
  findings: number
  high: number
  medium: number
  low: number
  requests: number
  git_refs: string[]
}

export interface TrendPoint {
  date: string
  /** Findings per conversation (rate) over the trailing window. */
  rate: number
  rateHigh: number
  rateMedium: number
  rateLow: number
  /** Raw counts within the window (for tooltips). */
  findings: number
  conversations: number
  high: number
  medium: number
  low: number
  /** Git refs active on this specific date (not the window). */
  git_refs: string[]
}

/** Group entries into per-date buckets, sorted by date ascending. */
export function buildDailyBuckets(entries: MetricsEntryInput[]): DailyBucket[] {
  const map = new Map<string, DailyBucket>()
  for (const e of entries) {
    let bucket = map.get(e.date)
    if (!bucket) {
      bucket = { date: e.date, conversations: 0, findings: 0, high: 0, medium: 0, low: 0, requests: 0, git_refs: [] }
      map.set(e.date, bucket)
    }
    bucket.conversations += 1
    bucket.findings += e.finding_count
    bucket.high += e.findings_by_severity?.high ?? 0
    bucket.medium += e.findings_by_severity?.medium ?? 0
    bucket.low += e.findings_by_severity?.low ?? 0
    bucket.requests += e.requests
    if (isDisplayableGitRef(e.git_ref) && !bucket.git_refs.includes(e.git_ref)) {
      bucket.git_refs.push(e.git_ref)
    }
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date))
}

/**
 * Enumerate every YYYY-MM-DD between start and end inclusive, in ascending
 * order. UTC-anchored to avoid local-timezone drift: local-midnight is not
 * UTC-midnight in any non-UTC zone, and `setDate` (local-time arithmetic)
 * can skip or duplicate days across DST transitions. Using `setUTCDate`
 * + `toISOString().slice(0, 10)` is uniform everywhere.
 */
export function datesBetween(startYmd: string, endYmd: string): string[] {
  const result: string[] = []
  const d = new Date(startYmd + 'T00:00:00Z')
  const end = new Date(endYmd + 'T00:00:00Z')
  while (d.getTime() <= end.getTime()) {
    result.push(d.toISOString().slice(0, 10))
    d.setUTCDate(d.getUTCDate() + 1)
  }
  return result
}

/**
 * Fill gaps in the date range so every day between the first and last bucket
 * has an entry (zero-conversation days get an empty bucket).
 */
function fillDateGaps(buckets: DailyBucket[]): DailyBucket[] {
  if (buckets.length <= 1) return buckets

  const byDate = new Map(buckets.map((b) => [b.date, b]))
  const dates = datesBetween(buckets[0].date, buckets[buckets.length - 1].date)
  return dates.map((ds) =>
    byDate.get(ds) ?? { date: ds, conversations: 0, findings: 0, high: 0, medium: 0, low: 0, requests: 0, git_refs: [] },
  )
}

/**
 * Compute rolling-window trend points.
 *
 * Each point aggregates the trailing `windowDays` days (inclusive of the
 * point's date).  Rate = findings / conversations over the window; days
 * with no conversations are skipped in the denominator.
 */
export function computeTrendPoints(
  buckets: DailyBucket[],
  windowDays: number,
): TrendPoint[] {
  const filled = fillDateGaps(buckets)
  if (filled.length === 0) return []

  return filled.map((_, i) => {
    const windowStart = Math.max(0, i - windowDays + 1)
    const window = filled.slice(windowStart, i + 1)

    let findings = 0, conversations = 0, high = 0, medium = 0, low = 0
    for (const b of window) {
      findings += b.findings
      conversations += b.conversations
      high += b.high
      medium += b.medium
      low += b.low
    }

    const rate = conversations > 0 ? findings / conversations : 0
    const rateHigh = conversations > 0 ? high / conversations : 0
    const rateMedium = conversations > 0 ? medium / conversations : 0
    const rateLow = conversations > 0 ? low / conversations : 0

    return {
      date: filled[i].date,
      rate, rateHigh, rateMedium, rateLow,
      findings, conversations, high, medium, low,
      git_refs: filled[i].git_refs,
    }
  })
}


// ─── Usage / cost trend data ────────────────────────────────────────────────

export interface UsageEntryInput {
  date: string
  git_ref: string
  coordinator_model?: string
  usage?: {
    input_tokens?: number
    output_tokens?: number
    cache_read_input_tokens?: number
    cache_creation_input_tokens?: number
    cost_usd?: number
  }
}

export interface UsageBucket {
  date: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  cost_usd: number
  git_refs: string[]
  models: string[]
}

export interface UsageTrendPoint {
  date: string
  /** Net input = input_tokens - cache_read_tokens (new prompt work the provider did). */
  net_input: number
  cache_read: number
  output: number
  cost_usd: number
  cache_hit_rate: number
  git_refs: string[]
  models: string[]
}

export function buildUsageBuckets(entries: UsageEntryInput[]): UsageBucket[] {
  const map = new Map<string, UsageBucket>()
  for (const e of entries) {
    let bucket = map.get(e.date)
    if (!bucket) {
      bucket = {
        date: e.date,
        input_tokens: 0, output_tokens: 0,
        cache_read_tokens: 0, cache_creation_tokens: 0,
        cost_usd: 0, git_refs: [], models: [],
      }
      map.set(e.date, bucket)
    }
    const u = e.usage ?? {}
    bucket.input_tokens += u.input_tokens ?? 0
    bucket.output_tokens += u.output_tokens ?? 0
    bucket.cache_read_tokens += u.cache_read_input_tokens ?? 0
    bucket.cache_creation_tokens += u.cache_creation_input_tokens ?? 0
    bucket.cost_usd += u.cost_usd ?? 0
    if (isDisplayableGitRef(e.git_ref) && !bucket.git_refs.includes(e.git_ref)) {
      bucket.git_refs.push(e.git_ref)
    }
    if (e.coordinator_model && !bucket.models.includes(e.coordinator_model)) {
      bucket.models.push(e.coordinator_model)
    }
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date))
}

function fillUsageDateGaps(buckets: UsageBucket[]): UsageBucket[] {
  if (buckets.length <= 1) return buckets
  const byDate = new Map(buckets.map((b) => [b.date, b]))
  const dates = datesBetween(buckets[0].date, buckets[buckets.length - 1].date)
  return dates.map((ds) =>
    byDate.get(ds) ?? {
      date: ds,
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_creation_tokens: 0,
      cost_usd: 0, git_refs: [], models: [],
    },
  )
}

export function computeUsageTrendPoints(
  buckets: UsageBucket[],
  windowDays: number,
): UsageTrendPoint[] {
  const filled = fillUsageDateGaps(buckets)
  if (filled.length === 0) return []

  return filled.map((_, i) => {
    const windowStart = Math.max(0, i - windowDays + 1)
    const window = filled.slice(windowStart, i + 1)

    let input = 0, output = 0, cacheRead = 0, cacheCreation = 0, cost = 0
    for (const b of window) {
      input += b.input_tokens
      output += b.output_tokens
      cacheRead += b.cache_read_tokens
      cacheCreation += b.cache_creation_tokens
      cost += b.cost_usd
    }

    // cache_hit_rate = cache_read / (input + cache_creation)
    // Denominator is the total 'new work' the provider processed;
    // cache_read is the portion served from cache.
    const denom = input + cacheCreation
    const cache_hit_rate = denom > 0 ? cacheRead / denom : 0

    return {
      date: filled[i].date,
      net_input: Math.max(0, input - cacheRead),
      cache_read: cacheRead,
      output,
      cost_usd: cost,
      cache_hit_rate,
      git_refs: filled[i].git_refs,
      models: filled[i].models,
    }
  })
}
