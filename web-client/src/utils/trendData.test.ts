import { describe, it, expect } from 'vitest'
import {
  buildDailyBuckets, computeTrendPoints, type MetricsEntryInput,
  buildUsageBuckets, computeUsageTrendPoints, type UsageEntryInput,
} from './trendData'

const entry = (
  date: string,
  finding_count: number,
  severity: Record<string, number> = {},
  requests = 3,
  git_ref = 'abc1234',
): MetricsEntryInput => ({
  date,
  finding_count,
  findings_by_severity: severity,
  requests,
  git_ref,
})

// ---------------------------------------------------------------------------
// buildDailyBuckets
// ---------------------------------------------------------------------------

describe('buildDailyBuckets', () => {
  it('groups entries by date and sums counts', () => {
    const entries = [
      entry('2026-03-29', 2, { high: 1, low: 1 }),
      entry('2026-03-29', 1, { medium: 1 }),
      entry('2026-03-28', 0, {}),
    ]
    const buckets = buildDailyBuckets(entries)
    expect(buckets).toHaveLength(2)
    // sorted ascending
    expect(buckets[0].date).toBe('2026-03-28')
    expect(buckets[1].date).toBe('2026-03-29')
    expect(buckets[1].conversations).toBe(2)
    expect(buckets[1].findings).toBe(3)
    expect(buckets[1].high).toBe(1)
    expect(buckets[1].medium).toBe(1)
    expect(buckets[1].low).toBe(1)
  })

  it('collects unique git refs per date', () => {
    const entries = [
      entry('2026-03-29', 0, {}, 1, 'aaa'),
      entry('2026-03-29', 0, {}, 1, 'bbb'),
      entry('2026-03-29', 0, {}, 1, 'aaa'), // duplicate
    ]
    const buckets = buildDailyBuckets(entries)
    expect(buckets[0].git_refs).toEqual(['aaa', 'bbb'])
  })

  it('returns empty for no entries', () => {
    expect(buildDailyBuckets([])).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// computeTrendPoints
// ---------------------------------------------------------------------------

describe('computeTrendPoints', () => {
  it('computes rate as findings/conversations', () => {
    const buckets = buildDailyBuckets([
      entry('2026-03-29', 4, { high: 2, medium: 1, low: 1 }),
      entry('2026-03-29', 2, { low: 2 }),
    ])
    // window=1: just this day. 2 conversations, 6 findings → rate 3.0
    const points = computeTrendPoints(buckets, 1)
    expect(points).toHaveLength(1)
    expect(points[0].rate).toBe(3)
    expect(points[0].rateHigh).toBe(1)
    expect(points[0].rateMedium).toBe(0.5)
    expect(points[0].rateLow).toBe(1.5)
  })

  it('rolling window aggregates trailing days', () => {
    const buckets = buildDailyBuckets([
      entry('2026-03-28', 2, { high: 2 }),
      entry('2026-03-29', 0, {}),
    ])
    // window=2: March 29 point includes both days
    // 2 conversations total, 2 findings → rate 1.0
    const points = computeTrendPoints(buckets, 2)
    const last = points[points.length - 1]
    expect(last.date).toBe('2026-03-29')
    expect(last.conversations).toBe(2)
    expect(last.findings).toBe(2)
    expect(last.rate).toBe(1)
  })

  it('fills date gaps with zero-conversation days', () => {
    const buckets = buildDailyBuckets([
      entry('2026-03-27', 1, { low: 1 }),
      entry('2026-03-29', 1, { low: 1 }),
    ])
    // Should have 3 points: 27, 28, 29
    const points = computeTrendPoints(buckets, 1)
    expect(points).toHaveLength(3)
    expect(points[1].date).toBe('2026-03-28')
    expect(points[1].conversations).toBe(0)
    expect(points[1].rate).toBe(0) // no conversations → rate 0
  })

  it('window larger than data range uses all available', () => {
    const buckets = buildDailyBuckets([
      entry('2026-03-28', 1, { low: 1 }),
      entry('2026-03-29', 1, { low: 1 }),
    ])
    // window=7 but only 2 days of data
    const points = computeTrendPoints(buckets, 7)
    expect(points).toHaveLength(2)
    // Last point aggregates both days: 2 findings / 2 conversations = 1.0
    expect(points[1].rate).toBe(1)
  })

  it('preserves git_refs from the specific date only', () => {
    const buckets = buildDailyBuckets([
      entry('2026-03-28', 0, {}, 1, 'old_ref'),
      entry('2026-03-29', 0, {}, 1, 'new_ref'),
    ])
    const points = computeTrendPoints(buckets, 3)
    // git_refs should be per-date, not aggregated across window
    expect(points[0].git_refs).toEqual(['old_ref'])
    expect(points[1].git_refs).toEqual(['new_ref'])
  })

  it('returns empty for empty input', () => {
    expect(computeTrendPoints([], 3)).toEqual([])
  })
})


// ---------------------------------------------------------------------------
// buildUsageBuckets / computeUsageTrendPoints
// ---------------------------------------------------------------------------

const usageEntry = (
  date: string,
  usage: Partial<{
    input_tokens: number
    output_tokens: number
    cache_read_input_tokens: number
    cache_creation_input_tokens: number
    cost_usd: number
  }> = {},
  git_ref = 'abc1234',
  coordinator_model?: string,
): UsageEntryInput => ({ date, git_ref, coordinator_model, usage })

describe('buildUsageBuckets', () => {
  it('sums tokens and cost by date', () => {
    const buckets = buildUsageBuckets([
      usageEntry('2026-04-17', { input_tokens: 1000, output_tokens: 200, cost_usd: 0.01 }),
      usageEntry('2026-04-17', { input_tokens: 500, cache_read_input_tokens: 300, cost_usd: 0.005 }),
      usageEntry('2026-04-16', { input_tokens: 2000, cache_creation_input_tokens: 100, cost_usd: 0.02 }),
    ])
    expect(buckets).toHaveLength(2)
    expect(buckets[0].date).toBe('2026-04-16')
    expect(buckets[1].input_tokens).toBe(1500)
    expect(buckets[1].output_tokens).toBe(200)
    expect(buckets[1].cache_read_tokens).toBe(300)
    expect(buckets[1].cost_usd).toBeCloseTo(0.015, 6)
  })

  it('treats missing usage block as zero', () => {
    const buckets = buildUsageBuckets([usageEntry('2026-04-17')])
    expect(buckets[0].input_tokens).toBe(0)
    expect(buckets[0].cost_usd).toBe(0)
  })

  it('collects unique git_refs and models per day', () => {
    const buckets = buildUsageBuckets([
      usageEntry('2026-04-17', {}, 'abc', 'anthropic/sonnet'),
      usageEntry('2026-04-17', {}, 'abc', 'anthropic/sonnet'),
      usageEntry('2026-04-17', {}, 'def', 'openai/gpt-5'),
    ])
    expect(buckets[0].git_refs).toEqual(['abc', 'def'])
    expect(buckets[0].models).toEqual(['anthropic/sonnet', 'openai/gpt-5'])
  })
})

describe('computeUsageTrendPoints', () => {
  it('computes net_input, cache_read, output, cost for a single date', () => {
    const buckets = buildUsageBuckets([
      usageEntry('2026-04-17', { input_tokens: 1500, output_tokens: 600, cache_read_input_tokens: 1200, cost_usd: 0.03 }),
    ])
    const points = computeUsageTrendPoints(buckets, 7)
    expect(points.length).toBeGreaterThanOrEqual(1)
    const p17 = points.find((p) => p.date === '2026-04-17')!
    expect(p17.net_input).toBe(300)  // 1500 - 1200
    expect(p17.cache_read).toBe(1200)
    expect(p17.output).toBe(600)
    expect(p17.cost_usd).toBeCloseTo(0.03, 6)
  })

  it('computes cache_hit_rate correctly', () => {
    const buckets = buildUsageBuckets([
      usageEntry('2026-04-17', {
        input_tokens: 1000, cache_read_input_tokens: 600, cache_creation_input_tokens: 100,
      }),
    ])
    const points = computeUsageTrendPoints(buckets, 1)
    // cache_read / (input + cache_creation) = 600 / (1000 + 100)
    expect(points[0].cache_hit_rate).toBeCloseTo(600 / 1100, 4)
  })

  it('returns zero hit rate when no input tokens', () => {
    const buckets = buildUsageBuckets([usageEntry('2026-04-17')])
    const points = computeUsageTrendPoints(buckets, 1)
    expect(points[0].cache_hit_rate).toBe(0)
  })

  it('preserves models per date for model-swap markers', () => {
    const buckets = buildUsageBuckets([
      usageEntry('2026-04-17', {}, 'abc', 'anthropic/sonnet'),
      usageEntry('2026-04-17', {}, 'abc', 'openai/gpt-5'),
    ])
    const points = computeUsageTrendPoints(buckets, 1)
    const p17 = points.find((p) => p.date === '2026-04-17')
    expect(p17?.models).toEqual(['anthropic/sonnet', 'openai/gpt-5'])
  })
})
