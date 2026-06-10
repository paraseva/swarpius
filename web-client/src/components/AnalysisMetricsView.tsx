import React from 'react'
import { computeDateLabels } from '../utils/computeDateLabels'
import {
  buildDailyBuckets, computeTrendPoints, type TrendPoint,
  buildUsageBuckets, computeUsageTrendPoints, type UsageTrendPoint,
} from '../utils/trendData'
import {
  FAILURE_MODE_DESCRIPTIONS,
  SEVERITY_COLOURS,
  formatModelOption,
  type MetricsEntry,
  type MetricsResponse,
} from './AnalysisBrowser.shared'
import { isDisplayableGitRef } from '../utils/gitRef'
import s from './AnalysisBrowser.module.css'

// ---------------------------------------------------------------------------
// Shared trend-chart helpers
// ---------------------------------------------------------------------------

type WindowDays = 1 | 3 | 7

const WindowPicker: React.FC<{
  value: WindowDays
  onChange: (v: WindowDays) => void
}> = ({ value, onChange }) => (
  <div className={s.trendChartControls}>
    <span className={s.trendChartControlsLabel}>Window:</span>
    <select
      value={value}
      onChange={(e) => onChange(Number(e.target.value) as WindowDays)}
      className={s.filterSelect}
    >
      <option value={1}>1 day</option>
      <option value={3}>3 days</option>
      <option value={7}>7 days</option>
    </select>
  </div>
)

// ---------------------------------------------------------------------------
// Charts (pure SVG)
// ---------------------------------------------------------------------------

const SeverityDonutChart: React.FC<{
  bySeverity: Record<string, number>
  total: number
}> = ({ bySeverity, total }) => {
  if (total === 0) return null

  const size = 270
  const strokeWidth = 36
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const cx = size / 2
  const cy = size / 2

  const severityOrder = ['high', 'medium', 'low'] as const
  const segments: { key: string; count: number; colour: string; offset: number; length: number }[] = []
  let accumulated = 0

  for (const sev of severityOrder) {
    const count = bySeverity[sev] ?? 0
    if (count === 0) continue
    const fraction = count / total
    segments.push({
      key: sev,
      count,
      colour: SEVERITY_COLOURS[sev],
      offset: accumulated,
      length: fraction * circumference,
    })
    accumulated += fraction * circumference
  }

  return (
    <div className={s.chartDonutContainer}>
      <svg width="100%" viewBox={`0 0 ${size} ${size}`} preserveAspectRatio="xMidYMid meet">
        {/* Background ring */}
        <circle cx={cx} cy={cy} r={radius} fill="none" stroke="rgba(128,128,128,0.15)" strokeWidth={strokeWidth} />
        {segments.map((seg) => (
          <circle
            key={seg.key}
            cx={cx}
            cy={cy}
            r={radius}
            fill="none"
            stroke={seg.colour}
            strokeWidth={strokeWidth}
            strokeDasharray={`${seg.length} ${circumference - seg.length}`}
            strokeDashoffset={-seg.offset}
            strokeLinecap="butt"
            transform={`rotate(-90 ${cx} ${cy})`}
          >
            <title>{seg.key}: {seg.count} ({((seg.count / total) * 100).toFixed(0)}%)</title>
          </circle>
        ))}
        <text x={cx} y={cy - 7} textAnchor="middle" className={s.chartDonutTotal}>{total}</text>
        <text x={cx} y={cy + 12} textAnchor="middle" className={s.chartDonutLabel}>findings</text>
      </svg>
    </div>
  )
}

const FailureModeBarChart: React.FC<{
  modeEntries: [string, number][]
  total: number
}> = ({ modeEntries, total }) => {
  if (modeEntries.length === 0) {
    return <div className={s.metricsEmpty}>No findings in the selected range.</div>
  }

  const maxCount = modeEntries[0]?.[1] ?? 1
  const barHeight = 8
  const gap = 2
  const labelWidth = 42
  const countWidth = 40
  const chartWidth = 140
  const svgWidth = labelWidth + chartWidth + countWidth
  const svgHeight = modeEntries.length * (barHeight + gap) - gap

  return (
    <div className={s.chartBarContainer}>
      <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="xMinYMin meet">
        {modeEntries.map(([mode, count], i) => {
          const y = i * (barHeight + gap)
          const barW = maxCount > 0 ? (count / maxCount) * chartWidth : 0
          const pct = total > 0 ? ((count / total) * 100).toFixed(0) : '0'
          const desc = FAILURE_MODE_DESCRIPTIONS[mode] ?? mode
          return (
            <g key={mode}>
              <title>{`${mode}: ${desc}\n${count} finding${count !== 1 ? 's' : ''} (${pct}%)`}</title>
              <text
                x={labelWidth - 4}
                y={y + barHeight / 2}
                textAnchor="end"
                dominantBaseline="central"
                className={s.chartBarLabel}
              >
                {mode}
              </text>
              <rect
                x={labelWidth}
                y={y}
                width={Math.max(barW, 2)}
                height={barHeight}
                rx={3}
                className={s.chartBarFill}
              />
              <text
                x={labelWidth + chartWidth + 4}
                y={y + barHeight / 2}
                dominantBaseline="central"
                className={s.chartBarCount}
              >
                {count} ({pct}%)
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

const RevocationsByModeTable: React.FC<{
  allModes: string[]
  byMode: Record<string, number>
  revokedByMode: Record<string, number>
}> = ({ allModes, byMode, revokedByMode }) => {
  if (allModes.length === 0) {
    return <div className={s.metricsEmpty}>No findings or revocations in the selected range.</div>
  }
  // Filter to modes where at least one revocation occurred — modes
  // with zero revocations carry no signal for guide-tuning.
  const rows = allModes.filter((mode) => (revokedByMode[mode] ?? 0) > 0)
  if (rows.length === 0) {
    return <div className={s.metricsEmpty}>No revocations in the selected range.</div>
  }
  return (
    <table className={s.metricsRevocationsTable}>
      <thead>
        <tr>
          <th>Mode</th>
          <th>Emitted</th>
          <th>Revoked</th>
          <th>Rate</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((mode) => {
          const emitted = byMode[mode] ?? 0
          const revoked = revokedByMode[mode] ?? 0
          const total = emitted + revoked
          const rate = total > 0 ? (revoked / total) * 100 : 0
          const desc = FAILURE_MODE_DESCRIPTIONS[mode] ?? mode
          return (
            <tr key={mode} title={desc}>
              <td className={s.metricsRevocationsMode}>{mode}</td>
              <td>{emitted}</td>
              <td>{revoked}</td>
              <td>{rate.toFixed(0)}%</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Findings trend chart (SVG line chart)
// ---------------------------------------------------------------------------

const TREND_COLOURS = {
  total: 'var(--color-text-primary, #e2e8f0)',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#3b82f6',
  volume: 'rgba(128, 128, 128, 0.15)',
  grid: 'rgba(128, 128, 128, 0.2)',
  marker: 'rgba(128, 128, 128, 0.5)',
} as const

const FindingsTrendChart: React.FC<{
  entries: MetricsEntry[]
}> = ({ entries }) => {
  const [windowDays, setWindowDays] = React.useState<WindowDays>(3)
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null)
  const [markerHoverIdx, setMarkerHoverIdx] = React.useState<number | null>(null)

  const buckets = React.useMemo(() => buildDailyBuckets(entries), [entries])
  const points = React.useMemo(() => computeTrendPoints(buckets, windowDays), [buckets, windowDays])

  if (points.length < 2) {
    return (
      <div className={s.metricsEmpty}>
        Not enough data yet to draw a trend. At least two days of analysis are needed.
      </div>
    )
  }

  const firstConvIdx = points.findIndex((p) => p.conversations > 0)
  const findingsStartNote = firstConvIdx > 0 ? points[firstConvIdx].date : null

  const marginLeft = 36
  const marginRight = 16
  const marginTop = 12
  const marginBottom = 40
  const chartWidth = 400
  const chartHeight = 160
  const svgWidth = marginLeft + chartWidth + marginRight
  const svgHeight = marginTop + chartHeight + marginBottom

  const maxRate = Math.max(...points.map((p) => p.rate), 0.5) * 1.15
  const maxConv = Math.max(...points.map((p) => p.conversations), 1)

  const x = (i: number) => marginLeft + (i / (points.length - 1)) * chartWidth
  const yRate = (v: number) => marginTop + chartHeight - (v / maxRate) * chartHeight
  const yConv = (v: number) => marginTop + chartHeight - (v / maxConv) * chartHeight

  const makeLine = (accessor: (p: TrendPoint) => number) =>
    points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${yRate(accessor(p)).toFixed(1)}`).join(' ')

  const lineTotal = makeLine((p) => p.rate)
  const lineHigh = makeLine((p) => p.rateHigh)
  const lineMedium = makeLine((p) => p.rateMedium)
  const lineLow = makeLine((p) => p.rateLow)

  const barWidth = Math.max(chartWidth / points.length * 0.6, 2)

  const yTickCount = 4
  const yTicks = Array.from({ length: yTickCount + 1 }, (_, i) => (maxRate / yTickCount) * i)

  const refMarkers: { idx: number; refs: string[] }[] = []
  let prevRefs = ''
  for (let i = 0; i < points.length; i++) {
    const refsKey = points[i].git_refs.sort().join(',')
    if (refsKey && refsKey !== prevRefs) {
      refMarkers.push({ idx: i, refs: points[i].git_refs })
      prevRefs = refsKey
    }
  }

  const dateLabels = computeDateLabels(points)

  const hoverPoint = hoverIdx != null ? points[hoverIdx] : null
  const markerPoint = markerHoverIdx != null ? refMarkers[markerHoverIdx] : null

  return (
    <div className={s.trendChartWrapper}>
      <WindowPicker value={windowDays} onChange={setWindowDays} />

      {findingsStartNote ? (
        <div className={s.trendChartStartNote}>Findings data starts {findingsStartNote}.</div>
      ) : null}

      <div className={s.trendLegend}>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: TREND_COLOURS.total }} />Total</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: TREND_COLOURS.high }} />High</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: TREND_COLOURS.medium }} />Medium</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: TREND_COLOURS.low }} />Low</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: TREND_COLOURS.volume, border: '1px solid rgba(128,128,128,0.3)' }} />Conversations</span>
      </div>

      <div className={s.trendChartContainer} style={{ position: 'relative' }}>
        <svg
          width="100%"
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          preserveAspectRatio="xMidYMid meet"
          className={s.trendChartSvg}
        >
          {yTicks.map((tick) => (
            <line
              key={tick}
              x1={marginLeft}
              x2={marginLeft + chartWidth}
              y1={yRate(tick)}
              y2={yRate(tick)}
              stroke={TREND_COLOURS.grid}
              strokeWidth={0.5}
            />
          ))}

          {yTicks.map((tick) => (
            <text
              key={tick}
              x={marginLeft - 4}
              y={yRate(tick)}
              textAnchor="end"
              dominantBaseline="central"
              className={s.trendAxisLabel}
            >
              {tick.toFixed(1)}
            </text>
          ))}

          {points.map((p, i) => (
            <rect
              key={i}
              x={x(i) - barWidth / 2}
              y={yConv(p.conversations)}
              width={barWidth}
              height={Math.max(marginTop + chartHeight - yConv(p.conversations), 0)}
              fill={TREND_COLOURS.volume}
              rx={1}
            />
          ))}

          {refMarkers.map((m, mi) => (
            <line
              key={m.idx}
              x1={x(m.idx)}
              x2={x(m.idx)}
              y1={marginTop}
              y2={marginTop + chartHeight}
              stroke={TREND_COLOURS.marker}
              strokeWidth={1}
              strokeDasharray="3,3"
              className={s.trendRefMarker}
              onMouseEnter={() => setMarkerHoverIdx(mi)}
              onMouseLeave={() => setMarkerHoverIdx(null)}
            />
          ))}

          <path d={lineHigh} fill="none" stroke={TREND_COLOURS.high} strokeWidth={1.5} opacity={0.7} />
          <path d={lineMedium} fill="none" stroke={TREND_COLOURS.medium} strokeWidth={1.5} opacity={0.7} />
          <path d={lineLow} fill="none" stroke={TREND_COLOURS.low} strokeWidth={1.5} opacity={0.7} />
          <path d={lineTotal} fill="none" stroke={TREND_COLOURS.total} strokeWidth={2} />

          {points.map((_, i) => {
            const hitW = chartWidth / points.length
            return (
              <rect
                key={i}
                x={x(i) - hitW / 2}
                y={marginTop}
                width={hitW}
                height={chartHeight}
                fill="transparent"
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)}
              />
            )
          })}

          {hoverIdx != null && (
            <line
              x1={x(hoverIdx)}
              x2={x(hoverIdx)}
              y1={marginTop}
              y2={marginTop + chartHeight}
              stroke={TREND_COLOURS.total}
              strokeWidth={0.5}
              opacity={0.5}
              pointerEvents="none"
            />
          )}

          {dateLabels.map(({ idx, label }) => (
            <text
              key={idx}
              x={x(idx)}
              y={marginTop + chartHeight + 14}
              textAnchor="middle"
              className={s.trendAxisLabel}
            >
              {label}
            </text>
          ))}

          {refMarkers.map((m, mi) => (
            <g
              key={`diamond-${m.idx}`}
              onMouseEnter={() => setMarkerHoverIdx(mi)}
              onMouseLeave={() => setMarkerHoverIdx(null)}
              className={s.trendRefDiamondGroup}
            >
              <polygon
                points={`${x(m.idx)},${marginTop + chartHeight + 24} ${x(m.idx) + 4},${marginTop + chartHeight + 28} ${x(m.idx)},${marginTop + chartHeight + 32} ${x(m.idx) - 4},${marginTop + chartHeight + 28}`}
                className={s.trendRefDiamond}
              />
            </g>
          ))}
        </svg>

        {hoverPoint && hoverIdx != null && (
          <div
            className={s.trendTooltip}
            style={{
              left: `${(x(hoverIdx) / svgWidth) * 100}%`,
              top: 0,
            }}
          >
            <div className={s.trendTooltipDate}>{hoverPoint.date}</div>
            <div className={s.trendTooltipRow}>
              <span>Rate</span><span>{hoverPoint.rate.toFixed(2)} findings/conv</span>
            </div>
            <div className={s.trendTooltipRow}>
              <span>Findings</span><span>{hoverPoint.findings} ({hoverPoint.high}H / {hoverPoint.medium}M / {hoverPoint.low}L)</span>
            </div>
            <div className={s.trendTooltipRow}>
              <span>Conversations</span><span>{hoverPoint.conversations}</span>
            </div>
            {hoverPoint.git_refs.length > 0 && (
              <div className={s.trendTooltipRow}>
                <span>Refs</span><span>{hoverPoint.git_refs.map((r) => r.slice(0, 7)).join(', ')}</span>
              </div>
            )}
          </div>
        )}

        {markerPoint && (
          <div
            className={s.trendTooltip}
            style={{
              left: `${(x(markerPoint.idx) / svgWidth) * 100}%`,
              bottom: 0,
            }}
          >
            <div className={s.trendTooltipDate}>Commit{markerPoint.refs.length > 1 ? 's' : ''}</div>
            {markerPoint.refs.map((ref) => (
              <div key={ref} className={s.trendTooltipRef}>{ref.slice(0, 7)}</div>
            ))}
          </div>
        )}
      </div>

    </div>
  )
}

const CollapsibleSection: React.FC<{
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}> = ({ title, defaultOpen = true, children }) => {
  const [open, setOpen] = React.useState(defaultOpen)
  return (
    <div className={s.metricsSection}>
      <button
        type="button"
        className={s.metricsSectionToggle}
        onClick={() => setOpen(!open)}
      >
        <span className={s.metricsSectionTitle}>{title}</span>
        <svg
          className={`${s.metricsSectionChevron} ${open ? s.expanded : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {open && children}
    </div>
  )
}

// ─── Usage / cost chart colours ────────────────────────────────────────────

const USAGE_COLOURS = {
  netInput: '#4a90e2',
  cacheRead: '#7ed6df',
  output: '#e1b84a',
  cost: '#d9534f',
  costBar: '#e08a87',
  rate: '#5cb85c',
  axis: 'rgba(180,180,180,0.6)',
  grid: 'rgba(128,128,128,0.15)',
  marker: 'rgba(200,200,200,0.5)',
  modelMarker: '#b57edc',
}

const USAGE_CHART_LAYOUT = {
  marginLeft: 46,
  marginRight: 46,
  marginTop: 12,
  marginBottom: 44,
  chartWidth: 400,
  chartHeight: 160,
}

const makeGitRefMarkers = (points: { git_refs: string[] }[]) => {
  const markers: { idx: number; refs: string[] }[] = []
  let prev = ''
  for (let i = 0; i < points.length; i++) {
    const key = [...points[i].git_refs].sort().join(',')
    if (key && key !== prev) {
      markers.push({ idx: i, refs: points[i].git_refs })
      prev = key
    }
  }
  return markers
}

const makeModelMarkers = (points: { models: string[] }[]) => {
  const markers: { idx: number; models: string[] }[] = []
  let prev = ''
  for (let i = 0; i < points.length; i++) {
    const key = [...points[i].models].sort().join(',')
    if (key && key !== prev) {
      markers.push({ idx: i, models: points[i].models })
      prev = key
    }
  }
  return markers
}

// ─── Token usage + cost trend chart ─────────────────────────────────────────

const TokenUsageTrendChart: React.FC<{ entries: MetricsEntry[] }> = ({ entries }) => {
  const [windowDays, setWindowDays] = React.useState<WindowDays>(3)
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null)

  const buckets = React.useMemo(() => buildUsageBuckets(entries), [entries])
  const points = React.useMemo(() => computeUsageTrendPoints(buckets, windowDays), [buckets, windowDays])

  if (points.length < 2) {
    return (
      <div className={s.metricsEmpty}>
        Not enough data yet to draw a trend. At least two days of usage are needed.
      </div>
    )
  }

  const firstCostIdx = points.findIndex((p) => p.cost_usd > 0)
  const costDataStartNote =
    firstCostIdx > 0 ? points[firstCostIdx].date : null

  const { marginLeft, marginRight, marginTop, marginBottom, chartWidth, chartHeight } = USAGE_CHART_LAYOUT
  const svgWidth = marginLeft + chartWidth + marginRight
  const svgHeight = marginTop + chartHeight + marginBottom

  const maxTokens = Math.max(...points.map((p) => p.net_input + p.cache_read + p.output), 1) * 1.15
  const maxCost = Math.max(...points.map((p) => p.cost_usd), 0.001) * 1.15

  const x = (i: number) => marginLeft + (i / (points.length - 1)) * chartWidth
  const yTokens = (v: number) => marginTop + chartHeight - (v / maxTokens) * chartHeight
  const yCost = (v: number) => marginTop + chartHeight - (v / maxCost) * chartHeight

  const stackPath = (accessor: (p: UsageTrendPoint) => number, baseline: (p: UsageTrendPoint) => number) => {
    const top = points.map((p, i) => `${x(i).toFixed(1)},${yTokens(baseline(p) + accessor(p)).toFixed(1)}`)
    const bottom = points.map((p, i) => `${x(i).toFixed(1)},${yTokens(baseline(p)).toFixed(1)}`).reverse()
    return `M${top.join(' L')} L${bottom.join(' L')} Z`
  }
  const areaNetInput = stackPath((p) => p.net_input, () => 0)
  const areaCacheRead = stackPath((p) => p.cache_read, (p) => p.net_input)
  const areaOutput = stackPath((p) => p.output, (p) => p.net_input + p.cache_read)
  const costLine = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${yCost(p.cost_usd).toFixed(1)}`).join(' ')

  const yTicks = Array.from({ length: 5 }, (_, i) => (maxTokens / 4) * i)
  const costTicks = Array.from({ length: 5 }, (_, i) => (maxCost / 4) * i)

  const refMarkers = makeGitRefMarkers(points)
  const modelMarkers = makeModelMarkers(points)

  const dateLabels = computeDateLabels(points)

  const hoverPoint = hoverIdx != null ? points[hoverIdx] : null
  const formatTokens = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toFixed(0)

  return (
    <div className={s.trendChartWrapper}>
      <WindowPicker value={windowDays} onChange={setWindowDays} />

      {costDataStartNote ? (
        <div className={s.trendChartStartNote}>Cost data starts {costDataStartNote}.</div>
      ) : null}

      <div className={s.trendLegend}>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: USAGE_COLOURS.netInput }} />Net input</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: USAGE_COLOURS.cacheRead }} />Cache read</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: USAGE_COLOURS.output }} />Output</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: USAGE_COLOURS.cost }} />Cost (USD)</span>
      </div>

      <div className={s.trendChartContainer} style={{ position: 'relative' }}>
        <svg
          width="100%"
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          preserveAspectRatio="xMidYMid meet"
          className={s.trendChartSvg}
        >
          {yTicks.map((tick) => (
            <line key={tick} x1={marginLeft} x2={marginLeft + chartWidth} y1={yTokens(tick)} y2={yTokens(tick)} stroke={USAGE_COLOURS.grid} strokeWidth={0.5} />
          ))}
          {yTicks.map((tick) => (
            <text key={tick} x={marginLeft - 4} y={yTokens(tick)} textAnchor="end" dominantBaseline="central" className={s.trendAxisLabel}>
              {formatTokens(tick)}
            </text>
          ))}
          {costTicks.map((tick) => (
            <text key={tick} x={marginLeft + chartWidth + 4} y={yCost(tick)} textAnchor="start" dominantBaseline="central" className={s.trendAxisLabel} style={{ fill: USAGE_COLOURS.cost }}>
              ${tick.toFixed(3)}
            </text>
          ))}

          {refMarkers.map((m) => (
            <line key={`ref-${m.idx}`} x1={x(m.idx)} x2={x(m.idx)} y1={marginTop} y2={marginTop + chartHeight} stroke={USAGE_COLOURS.marker} strokeWidth={1} strokeDasharray="3,3" />
          ))}
          {modelMarkers.map((m) => (
            <line key={`model-${m.idx}`} x1={x(m.idx)} x2={x(m.idx)} y1={marginTop} y2={marginTop + chartHeight} stroke={USAGE_COLOURS.modelMarker} strokeWidth={1} strokeDasharray="5,2" />
          ))}

          <path d={areaNetInput} fill={USAGE_COLOURS.netInput} opacity={0.7} />
          <path d={areaCacheRead} fill={USAGE_COLOURS.cacheRead} opacity={0.7} />
          <path d={areaOutput} fill={USAGE_COLOURS.output} opacity={0.7} />
          <path d={costLine} fill="none" stroke={USAGE_COLOURS.cost} strokeWidth={2} />

          {points.map((_, i) => (
            <rect key={i} x={x(i) - 6} y={marginTop} width={12} height={chartHeight} fill="transparent" onMouseEnter={() => setHoverIdx(i)} onMouseLeave={() => setHoverIdx(null)} />
          ))}

          {dateLabels.map((d) => (
            <text key={d.idx} x={x(d.idx)} y={marginTop + chartHeight + 14} textAnchor="middle" className={s.trendAxisLabel}>
              {d.label}
            </text>
          ))}
        </svg>
        {hoverPoint && (
          <div className={s.trendTooltip} style={{ left: `${(x(hoverIdx!) / svgWidth) * 100}%` }}>
            <div><strong>{hoverPoint.date}</strong></div>
            <div>Net input: {formatTokens(hoverPoint.net_input)}</div>
            <div>Cache read: {formatTokens(hoverPoint.cache_read)}</div>
            <div>Output: {formatTokens(hoverPoint.output)}</div>
            <div>Cost: ${hoverPoint.cost_usd.toFixed(3)}</div>
            {hoverPoint.models.length > 0 && <div>{hoverPoint.models.map((m) => m.replace(/^[^/]+\//, '')).join(', ')}</div>}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Cache hit rate trend chart ─────────────────────────────────────────────

const CacheHitRateTrendChart: React.FC<{ entries: MetricsEntry[] }> = ({ entries }) => {
  const [windowDays, setWindowDays] = React.useState<WindowDays>(3)
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null)

  const buckets = React.useMemo(() => buildUsageBuckets(entries), [entries])
  const points = React.useMemo(() => computeUsageTrendPoints(buckets, windowDays), [buckets, windowDays])

  if (points.length < 2) {
    return (
      <div className={s.metricsEmpty}>
        Not enough data yet to draw a trend. At least two days of usage are needed.
      </div>
    )
  }

  const firstRateIdx = points.findIndex((p) => p.cache_hit_rate > 0)
  const rateDataStartNote =
    firstRateIdx > 0 ? points[firstRateIdx].date : null

  const { marginLeft, marginRight, marginTop, marginBottom, chartWidth, chartHeight } = USAGE_CHART_LAYOUT
  const svgWidth = marginLeft + chartWidth + marginRight
  const svgHeight = marginTop + chartHeight + marginBottom

  const x = (i: number) => marginLeft + (i / (points.length - 1)) * chartWidth
  const y = (v: number) => marginTop + chartHeight - v * chartHeight

  const rateLine = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.cache_hit_rate).toFixed(1)}`).join(' ')
  const yTicks = [0, 0.25, 0.5, 0.75, 1.0]

  const refMarkers = makeGitRefMarkers(points)
  const modelMarkers = makeModelMarkers(points)

  const dateLabels = computeDateLabels(points)

  const hoverPoint = hoverIdx != null ? points[hoverIdx] : null

  return (
    <div className={s.trendChartWrapper}>
      <WindowPicker value={windowDays} onChange={setWindowDays} />

      {rateDataStartNote ? (
        <div className={s.trendChartStartNote}>Cache-hit data starts {rateDataStartNote}.</div>
      ) : null}

      <div className={s.trendChartContainer} style={{ position: 'relative' }}>
        <svg
          width="100%"
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          preserveAspectRatio="xMidYMid meet"
          className={s.trendChartSvg}
        >
          {yTicks.map((tick) => (
            <line key={tick} x1={marginLeft} x2={marginLeft + chartWidth} y1={y(tick)} y2={y(tick)} stroke={USAGE_COLOURS.grid} strokeWidth={0.5} />
          ))}
          {yTicks.map((tick) => (
            <text key={tick} x={marginLeft - 4} y={y(tick)} textAnchor="end" dominantBaseline="central" className={s.trendAxisLabel}>
              {(tick * 100).toFixed(0)}%
            </text>
          ))}

          {refMarkers.map((m) => (
            <line key={`ref-${m.idx}`} x1={x(m.idx)} x2={x(m.idx)} y1={marginTop} y2={marginTop + chartHeight} stroke={USAGE_COLOURS.marker} strokeWidth={1} strokeDasharray="3,3" />
          ))}
          {modelMarkers.map((m) => (
            <line key={`model-${m.idx}`} x1={x(m.idx)} x2={x(m.idx)} y1={marginTop} y2={marginTop + chartHeight} stroke={USAGE_COLOURS.modelMarker} strokeWidth={1} strokeDasharray="5,2" />
          ))}

          <path d={rateLine} fill="none" stroke={USAGE_COLOURS.rate} strokeWidth={2} />

          {points.map((_, i) => (
            <rect key={i} x={x(i) - 6} y={marginTop} width={12} height={chartHeight} fill="transparent" onMouseEnter={() => setHoverIdx(i)} onMouseLeave={() => setHoverIdx(null)} />
          ))}

          {dateLabels.map((d) => (
            <text key={d.idx} x={x(d.idx)} y={marginTop + chartHeight + 14} textAnchor="middle" className={s.trendAxisLabel}>
              {d.label}
            </text>
          ))}
        </svg>
        {hoverPoint && (
          <div className={s.trendTooltip} style={{ left: `${(x(hoverIdx!) / svgWidth) * 100}%` }}>
            <div><strong>{hoverPoint.date}</strong></div>
            <div>Hit rate: {(hoverPoint.cache_hit_rate * 100).toFixed(1)}%</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Cost by request shape chart ────────────────────────────────────────────

interface ShapeBucket {
  label: string
  match: (steps: number) => boolean
}

const REQUEST_SHAPE_BUCKETS: ShapeBucket[] = [
  { label: 'Simple (1-2 steps)', match: (s) => s >= 1 && s <= 2 },
  { label: 'Compound (3-4 steps)', match: (s) => s >= 3 && s <= 4 },
  { label: 'Complex (5+ steps)', match: (s) => s >= 5 },
]

const CostByShapeChart: React.FC<{ entries: MetricsEntry[] }> = ({ entries }) => {
  const buckets = REQUEST_SHAPE_BUCKETS.map((b) => {
    const matching = entries.filter((e) => {
      const steps = Math.round(e.avg_steps ?? 0)
      return b.match(steps)
    })
    const totalRequests = matching.reduce((sum, e) => sum + (e.requests ?? 0), 0)
    const totalCost = matching.reduce((sum, e) => sum + (e.usage?.cost_usd ?? 0), 0)
    const meanCost = totalRequests > 0 ? totalCost / totalRequests : 0
    return { label: b.label, meanCost, requestCount: totalRequests }
  })

  const maxCost = Math.max(...buckets.map((b) => b.meanCost), 0.001)
  const bucketsWithData = buckets.filter((b) => b.requestCount > 0 && b.meanCost > 0)
  const anyData = bucketsWithData.length > 0

  if (!anyData) {
    return (
      <div className={s.metricsEmpty}>
        No cost data in the selected range.
      </div>
    )
  }

  const missingLabels = buckets
    .filter((b) => b.requestCount === 0 || b.meanCost === 0)
    .map((b) => b.label.replace(/\s*\(.*\)$/, ''))
  const partialNote =
    bucketsWithData.length < buckets.length
      ? `No cost data for ${missingLabels.join(' or ')} in this range.`
      : null

  return (
    <div className={s.shapeChartWrapper}>
      {buckets.map((b) => {
        const widthPct = b.meanCost > 0 ? (b.meanCost / maxCost) * 100 : 0
        const dim = b.meanCost === 0
        return (
          <div key={b.label} className={`${s.shapeBar} ${dim ? s.shapeBarDim : ''}`}>
            <div className={s.shapeBarLabel}>{b.label}</div>
            <div className={s.shapeBarTrack}>
              <div
                className={s.shapeBarFill}
                style={{ width: `${widthPct}%`, background: USAGE_COLOURS.costBar }}
              />
              <span className={s.shapeBarValue}>
                {b.meanCost > 0 ? `$${b.meanCost.toFixed(4)}/req` : '—'}
                {' · '}
                {b.requestCount} req{b.requestCount === 1 ? '' : 's'}
              </span>
            </div>
          </div>
        )
      })}
      {partialNote ? <div className={s.shapePartialNote}>{partialNote}</div> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Top-level metrics view
// ---------------------------------------------------------------------------

export const AnalysisMetricsView: React.FC<{
  metrics: MetricsResponse | null
  loading: boolean
  after: string
  before: string
  gitRef: string
  model: string
  onAfterChange: (v: string) => void
  onBeforeChange: (v: string) => void
  onRefChange: (v: string) => void
  onModelChange: (v: string) => void
}> = ({ metrics, loading, after, before, gitRef, model, onAfterChange, onBeforeChange, onRefChange, onModelChange }) => {
  const severityOrder = ['high', 'medium', 'low'] as const
  const bySeverity = metrics?.findings_by_severity ?? {}
  const byMode = metrics?.findings_by_mode ?? {}
  const revokedByMode = metrics?.revoked_by_mode ?? {}
  const modeEntries = Object.entries(byMode).sort(([, a], [, b]) => b - a)
  const totalFindings = metrics?.total_findings ?? 0
  const totalRevoked = metrics?.total_revoked ?? 0
  const revocationDenominator = totalFindings + totalRevoked
  const revocationRate = revocationDenominator > 0
    ? (totalRevoked / revocationDenominator) * 100
    : 0
  // Combine emitted + revoked failure-mode sets so the breakdown table
  // shows every mode that surfaced, even when one of the two is zero.
  const allModes = Array.from(
    new Set([...Object.keys(byMode), ...Object.keys(revokedByMode)]),
  ).sort((a, b) => {
    const totalA = (byMode[a] ?? 0) + (revokedByMode[a] ?? 0)
    const totalB = (byMode[b] ?? 0) + (revokedByMode[b] ?? 0)
    return totalB - totalA
  })
  const totalInput = metrics?.total_input_tokens ?? 0
  const totalOutput = metrics?.total_output_tokens ?? 0
  const totalCacheRead = metrics?.total_cache_read_tokens ?? 0
  const totalCost = metrics?.total_cost_usd ?? 0
  const cacheHitRate = metrics?.cache_hit_rate ?? 0
  const [subtab, setSubtab] = React.useState<'findings' | 'costs'>('findings')

  return (
    <div className={s.analysisMetrics}>
      <div className={s.metricsHeader}>
        <div className={s.filterBar}>
          <label className={s.filterField}>
            <span>After</span>
            <input
              type="date"
              value={after}
              className={!after ? s.filterDateEmpty : ''}
              onChange={(e) => onAfterChange(e.target.value)}
            />
          </label>
          <label className={s.filterField}>
            <span>Before</span>
            <input
              type="date"
              value={before}
              className={!before ? s.filterDateEmpty : ''}
              onChange={(e) => onBeforeChange(e.target.value)}
            />
          </label>
          <label className={s.filterField}>
            <span>Git ref</span>
            <select
              value={gitRef}
              onChange={(e) => onRefChange(e.target.value)}
              className={s.filterSelect}
            >
              <option value="">All refs</option>
              {(metrics?.git_refs ?? []).filter(isDisplayableGitRef).map((ref) => (
                <option key={ref} value={ref}>{ref.slice(0, 7)}</option>
              ))}
            </select>
          </label>
          <label className={s.filterField}>
            <span>Model</span>
            <select
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
              className={s.filterSelect}
            >
              <option value="">All models</option>
              {(metrics?.models ?? []).map((m, _idx, arr) => (
                <option key={m} value={m}>{formatModelOption(m, arr)}</option>
              ))}
            </select>
          </label>
        </div>

        {metrics && (
          <>
            <div className={s.metricsSummaryCards}>
            <div className={`${s.metricsSummaryRow} ${s.metricsSummaryRowFour}`}>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>{metrics.total_conversations ?? 0}</div>
                <div className={s.metricsCardLabel}>Conversations</div>
              </div>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>{totalFindings}</div>
                <div className={s.metricsCardLabel}>Findings</div>
              </div>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>
                  {metrics.avg_steps_per_request && metrics.avg_steps_per_request > 0
                    ? metrics.avg_steps_per_request.toFixed(1)
                    : '—'}
                </div>
                <div className={s.metricsCardLabel}>Avg steps/req</div>
              </div>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>
                  {revocationDenominator > 0 ? `${revocationRate.toFixed(0)}%` : '—'}
                </div>
                <div className={s.metricsCardLabel}>
                  Revocation rate{totalRevoked > 0 ? ` (${totalRevoked}/${revocationDenominator})` : ''}
                </div>
              </div>
            </div>
            <div className={`${s.metricsSummaryRow} ${s.metricsSummaryRowThree}`}>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>
                  {(totalInput + totalOutput) > 0
                    ? (totalInput - totalCacheRead + totalOutput).toLocaleString()
                    : '—'}
                </div>
                <div className={s.metricsCardLabel}>Net tokens</div>
              </div>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>
                  {totalCost > 0 ? `$${totalCost.toFixed(3)}` : '—'}
                </div>
                <div className={s.metricsCardLabel}>Cost</div>
              </div>
              <div className={s.metricsCard}>
                <div className={s.metricsCardValue}>
                  {(totalInput + (metrics.total_cache_creation_tokens ?? 0)) > 0
                    ? `${(cacheHitRate * 100).toFixed(0)}%`
                    : '—'}
                </div>
                <div className={s.metricsCardLabel}>Cache hit rate</div>
              </div>
            </div>
          </div>

            <div className={s.metricsSubtabBar} role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={subtab === 'findings'}
                className={`${s.metricsSubtab} ${subtab === 'findings' ? s.metricsSubtabActive : ''}`}
                onClick={() => setSubtab('findings')}
              >
                Findings
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={subtab === 'costs'}
                className={`${s.metricsSubtab} ${subtab === 'costs' ? s.metricsSubtabActive : ''}`}
                onClick={() => setSubtab('costs')}
              >
                Costs
              </button>
            </div>
          </>
        )}
      </div>

      <div className={s.metricsBody}>
        {loading && !metrics && (
          <div className={s.analysisEmpty}>Loading metrics...</div>
        )}
        {metrics && (<>
          {subtab === 'findings' && (
            <>
              <CollapsibleSection title="Findings by severity">
                {totalFindings === 0 ? (
                  <div className={s.metricsEmpty}>No findings in the selected range.</div>
                ) : (
                  <>
                    <SeverityDonutChart bySeverity={bySeverity} total={totalFindings} />
                    <div className={s.metricsSeverityRow}>
                      {severityOrder.map((sev) => {
                        const count = bySeverity[sev] ?? 0
                        return (
                          <div key={sev} className={s.metricsSeverityItem}>
                            <span
                              className={s.metricsSeverityDot}
                              style={{ background: SEVERITY_COLOURS[sev] }}
                            />
                            <span className={s.metricsSeverityLabel}>{sev}</span>
                            <span className={s.metricsSeverityCount}>{count}</span>
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}
              </CollapsibleSection>

              <CollapsibleSection title="Findings by failure mode">
                <FailureModeBarChart modeEntries={modeEntries} total={totalFindings} />
              </CollapsibleSection>

              {totalRevoked > 0 && (
                <CollapsibleSection title="Revocations by failure mode">
                  <RevocationsByModeTable
                    allModes={allModes}
                    byMode={byMode}
                    revokedByMode={revokedByMode}
                  />
                </CollapsibleSection>
              )}

              <CollapsibleSection title="Findings rate over time">
                <FindingsTrendChart entries={metrics?.entries ?? []} />
              </CollapsibleSection>
            </>
          )}

          {subtab === 'costs' && (
            <>
              <CollapsibleSection title="Token usage &amp; cost over time">
                <TokenUsageTrendChart entries={metrics?.entries ?? []} />
              </CollapsibleSection>
              <CollapsibleSection title="Cache hit rate over time">
                <CacheHitRateTrendChart entries={metrics?.entries ?? []} />
              </CollapsibleSection>
              <CollapsibleSection title="Mean cost per request, by complexity">
                <CostByShapeChart entries={metrics?.entries ?? []} />
              </CollapsibleSection>
            </>
          )}

        </>)}
      </div>
    </div>
  )
}
