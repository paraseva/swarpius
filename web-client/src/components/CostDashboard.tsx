import React from 'react'
import { useWebSocket } from '../websocketContext'
import { createUuid } from '../utils/uuid'
import s from './AnalysisBrowser.module.css'

interface Metric {
  cost_usd: number
  input_tokens: number
  output_tokens: number
  cache_creation_tokens: number
  cache_read_tokens: number
  count: number
}

interface GroupRow extends Metric {
  key: string | null
}

interface CostMetrics {
  request_id?: string
  ok?: boolean
  error?: string
  total: Metric
  by_agent: GroupRow[]
  by_model: GroupRow[]
  by_conversation: GroupRow[]
  by_day: GroupRow[]
}

// The fixed set of LLM consumers (sub-agents are off by default; they only
// appear in the data when enabled, but the filter offers them regardless).
const AGENTS = ['Coordinator', 'Diagnostic', 'Arbiter', 'Analyser']
const DAY_MS = 86_400_000

const COLOURS = {
  netInput: '#4a90e2',
  cacheRead: '#7ed6df',
  output: '#e1b84a',
  cost: '#d9534f',
  grid: 'rgba(128,128,128,0.15)',
}
// Segment palette for the by-agent donut.
const PALETTE = ['#4a90e2', '#e1b84a', '#7ed6df', '#d9534f', '#b57edc', '#5cb85c']

const CHART = { marginLeft: 46, marginRight: 46, marginTop: 12, marginBottom: 44, chartWidth: 400, chartHeight: 160 }

function formatCost(n: number): string {
  if (!n) return '$0.00'
  return n < 1 ? `$${n.toFixed(n < 0.01 ? 4 : 3)}` : `$${n.toFixed(2)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return n.toFixed(0)
}

const netInputOf = (p: GroupRow) => Math.max(0, p.input_tokens - p.cache_read_tokens)

const CollapsibleSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
  const [open, setOpen] = React.useState(true)
  return (
    <div className={s.metricsSection}>
      <button type="button" className={s.metricsSectionToggle} onClick={() => setOpen(!open)}>
        <span className={s.metricsSectionTitle}>{title}</span>
        <svg className={`${s.metricsSectionChevron} ${open ? s.expanded : ''}`} viewBox="0 0 24 24"
             fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {open && children}
    </div>
  )
}

const CostTrendChart: React.FC<{ points: GroupRow[] }> = ({ points }) => {
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null)
  if (points.length < 2) {
    return <div className={s.metricsEmpty}>Not enough data to draw a trend — at least two days are needed.</div>
  }
  const { marginLeft, marginRight, marginTop, marginBottom, chartWidth, chartHeight } = CHART
  const svgWidth = marginLeft + chartWidth + marginRight
  const svgHeight = marginTop + chartHeight + marginBottom

  const maxTokens = Math.max(...points.map((p) => netInputOf(p) + p.cache_read_tokens + p.output_tokens), 1) * 1.15
  const maxCost = Math.max(...points.map((p) => p.cost_usd), 0.001) * 1.15
  const x = (i: number) => marginLeft + (i / (points.length - 1)) * chartWidth
  const yT = (v: number) => marginTop + chartHeight - (v / maxTokens) * chartHeight
  const yC = (v: number) => marginTop + chartHeight - (v / maxCost) * chartHeight

  const stack = (acc: (p: GroupRow) => number, base: (p: GroupRow) => number) => {
    const top = points.map((p, i) => `${x(i).toFixed(1)},${yT(base(p) + acc(p)).toFixed(1)}`)
    const bottom = points.map((p, i) => `${x(i).toFixed(1)},${yT(base(p)).toFixed(1)}`).reverse()
    return `M${top.join(' L')} L${bottom.join(' L')} Z`
  }
  const areaNet = stack(netInputOf, () => 0)
  const areaCache = stack((p) => p.cache_read_tokens, netInputOf)
  const areaOut = stack((p) => p.output_tokens, (p) => netInputOf(p) + p.cache_read_tokens)
  const costLine = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${yC(p.cost_usd).toFixed(1)}`).join(' ')

  const yTicks = Array.from({ length: 5 }, (_, i) => (maxTokens / 4) * i)
  const costTicks = Array.from({ length: 5 }, (_, i) => (maxCost / 4) * i)
  const labelEvery = Math.max(1, Math.ceil(points.length / 6))
  const dateLabels = points
    .map((p, i) => ({ idx: i, label: (p.key ?? '').slice(5) }))
    .filter((_, i) => i % labelEvery === 0 || i === points.length - 1)
  const hp = hoverIdx != null ? points[hoverIdx] : null

  return (
    <div className={s.trendChartWrapper}>
      <div className={s.trendLegend}>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: COLOURS.netInput }} />Net input</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: COLOURS.cacheRead }} />Cache read</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: COLOURS.output }} />Output</span>
        <span className={s.trendLegendItem}><span className={s.trendLegendSwatch} style={{ background: COLOURS.cost }} />Cost (USD)</span>
      </div>
      <div className={s.trendChartContainer} style={{ position: 'relative' }}>
        <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="xMidYMid meet" className={s.trendChartSvg}>
          {yTicks.map((t) => (
            <line key={t} x1={marginLeft} x2={marginLeft + chartWidth} y1={yT(t)} y2={yT(t)} stroke={COLOURS.grid} strokeWidth={0.5} />
          ))}
          {yTicks.map((t) => (
            <text key={t} x={marginLeft - 4} y={yT(t)} textAnchor="end" dominantBaseline="central" className={s.trendAxisLabel}>{formatTokens(t)}</text>
          ))}
          {costTicks.map((t) => (
            <text key={t} x={marginLeft + chartWidth + 4} y={yC(t)} textAnchor="start" dominantBaseline="central" className={s.trendAxisLabel} style={{ fill: COLOURS.cost }}>{formatCost(t)}</text>
          ))}
          <path d={areaNet} fill={COLOURS.netInput} opacity={0.7} />
          <path d={areaCache} fill={COLOURS.cacheRead} opacity={0.7} />
          <path d={areaOut} fill={COLOURS.output} opacity={0.7} />
          <path d={costLine} fill="none" stroke={COLOURS.cost} strokeWidth={2} />
          {points.map((_, i) => (
            <rect key={i} x={x(i) - 6} y={marginTop} width={12} height={chartHeight} fill="transparent"
                  onMouseEnter={() => setHoverIdx(i)} onMouseLeave={() => setHoverIdx(null)} />
          ))}
          {dateLabels.map((d) => (
            <text key={d.idx} x={x(d.idx)} y={marginTop + chartHeight + 14} textAnchor="middle" className={s.trendAxisLabel}>{d.label}</text>
          ))}
        </svg>
        {hp && (
          <div className={s.trendTooltip} style={{ left: `${(x(hoverIdx!) / svgWidth) * 100}%` }}>
            <div><strong>{hp.key}</strong></div>
            <div>Net input: {formatTokens(netInputOf(hp))}</div>
            <div>Cache read: {formatTokens(hp.cache_read_tokens)}</div>
            <div>Output: {formatTokens(hp.output_tokens)}</div>
            <div>Cost: {formatCost(hp.cost_usd)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

const CostDonut: React.FC<{ rows: GroupRow[] }> = ({ rows }) => {
  const segs = rows.filter((r) => r.cost_usd > 0)
  const total = segs.reduce((sum, r) => sum + r.cost_usd, 0)
  if (total <= 0) return <div className={s.metricsEmpty}>No cost in the selected range.</div>

  const size = 270, strokeWidth = 36, radius = (size - strokeWidth) / 2
  const circ = 2 * Math.PI * radius, cx = size / 2, cy = size / 2
  const arcs: { key: string; cost: number; colour: string; offset: number; length: number; frac: number }[] = []
  let acc = 0
  for (let i = 0; i < segs.length; i++) {
    const r = segs[i]
    const frac = r.cost_usd / total
    arcs.push({ key: r.key ?? '—', cost: r.cost_usd, colour: PALETTE[i % PALETTE.length], offset: acc, length: frac * circ, frac })
    acc += frac * circ
  }
  return (
    <>
      <div className={s.chartDonutContainer}>
        <svg width="100%" viewBox={`0 0 ${size} ${size}`} preserveAspectRatio="xMidYMid meet">
          <circle cx={cx} cy={cy} r={radius} fill="none" stroke="rgba(128,128,128,0.15)" strokeWidth={strokeWidth} />
          {arcs.map((arc) => (
            <circle key={arc.key} cx={cx} cy={cy} r={radius} fill="none" stroke={arc.colour} strokeWidth={strokeWidth}
                    strokeDasharray={`${arc.length} ${circ - arc.length}`} strokeDashoffset={-arc.offset}
                    strokeLinecap="butt" transform={`rotate(-90 ${cx} ${cy})`}>
              <title>{arc.key}: {formatCost(arc.cost)} ({(arc.frac * 100).toFixed(0)}%)</title>
            </circle>
          ))}
          <text x={cx} y={cy - 7} textAnchor="middle" className={s.chartDonutTotal}>{formatCost(total)}</text>
          <text x={cx} y={cy + 12} textAnchor="middle" className={s.chartDonutLabel}>total</text>
        </svg>
      </div>
      <div className={s.trendLegend}>
        {arcs.map((arc) => (
          <span key={arc.key} className={s.trendLegendItem}>
            <span className={s.trendLegendSwatch} style={{ background: arc.colour, width: '0.6rem', height: '0.6rem', borderRadius: '50%' }} />
            {arc.key} {formatCost(arc.cost)}
          </span>
        ))}
      </div>
    </>
  )
}

const CostBar: React.FC<{ rows: GroupRow[]; limit?: number; stripPrefix?: boolean }> = ({ rows, limit, stripPrefix }) => {
  const shown = (limit ? rows.slice(0, limit) : rows).filter((r) => r.cost_usd > 0)
  if (shown.length === 0) return <div className={s.metricsEmpty}>No cost in the selected range.</div>

  const maxCost = shown[0].cost_usd || 1
  const barHeight = 8, gap = 4, labelWidth = 72, countWidth = 52, chartW = 120
  const svgWidth = labelWidth + chartW + countWidth
  const svgHeight = shown.length * (barHeight + gap) - gap
  const label = (k: string | null) => {
    const v = k ?? '—'
    return stripPrefix ? v.replace(/^[^/]+\//, '') : v
  }
  return (
    <div className={s.chartBarContainer}>
      <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="xMinYMin meet">
        {shown.map((r, i) => {
          const y = i * (barHeight + gap)
          const w = maxCost > 0 ? (r.cost_usd / maxCost) * chartW : 0
          return (
            <g key={r.key ?? '—'}>
              <title>{`${label(r.key)}: ${formatCost(r.cost_usd)} (${r.count} req)`}</title>
              <text x={labelWidth - 4} y={y + barHeight / 2} textAnchor="end" dominantBaseline="central" className={s.chartBarLabel}>{label(r.key)}</text>
              <rect x={labelWidth} y={y} width={Math.max(w, 2)} height={barHeight} rx={3} className={s.chartBarFill} />
              <text x={labelWidth + chartW + 4} y={y + barHeight / 2} dominantBaseline="central" className={s.chartBarCount}>{formatCost(r.cost_usd)}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

export const CostDashboard: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { messages, sendMessage } = useWebSocket()
  const [after, setAfter] = React.useState('')
  const [before, setBefore] = React.useState('')
  const [agent, setAgent] = React.useState('')
  const [model, setModel] = React.useState('')
  const [metrics, setMetrics] = React.useState<CostMetrics | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')
  const [modelOptions, setModelOptions] = React.useState<string[]>([])
  const pendingRef = React.useRef<string | null>(null)
  const processedRef = React.useRef(0)

  const fetchMetrics = React.useCallback(() => {
    const rid = createUuid()
    pendingRef.current = rid
    setLoading(true)
    setError('')
    const payload: Record<string, unknown> = { request_id: rid }
    if (after) payload.since_ms = new Date(`${after}T00:00:00`).getTime()
    if (before) payload.until_ms = new Date(`${before}T00:00:00`).getTime() + DAY_MS
    if (agent) payload.agent = agent
    if (model) payload.model = model
    sendMessage('cost-metrics-request', JSON.stringify(payload))
  }, [sendMessage, after, before, agent, model])

  React.useEffect(() => {
    // Re-fetch when a filter changes; the setState (loading) inside is the
    // intended start-of-fetch, mirroring AnalysisBrowser's metrics fetch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchMetrics()
  }, [fetchMetrics])

  React.useEffect(() => {
    const start = processedRef.current
    const fresh = messages.slice(start)
    processedRef.current = messages.length
    for (const m of fresh) {
      if (m.channel !== 'cost-metrics-response' || m.direction !== 'inbound') continue
      let payload: CostMetrics | null = null
      const raw = m.payload ?? m.body
      if (raw && typeof raw === 'object') {
        payload = raw as CostMetrics
      } else if (typeof raw === 'string') {
        try { payload = JSON.parse(raw) as CostMetrics } catch { payload = null }
      }
      if (!payload || payload.request_id !== pendingRef.current) continue
      pendingRef.current = null
      setLoading(false)
      if (payload.ok === false) {
        setError(payload.error || 'Failed to load costs.')
        continue
      }
      setMetrics(payload)
      if (!model) {
        setModelOptions(payload.by_model.map((r) => r.key).filter((k): k is string => !!k))
      }
    }
  }, [messages, model])

  const total = metrics?.total
  const netTokens = total ? total.input_tokens - total.cache_read_tokens + total.output_tokens : 0
  const cacheHitRate = total && total.input_tokens > 0 ? total.cache_read_tokens / total.input_tokens : 0
  const hasData = !!total && total.count > 0

  return (
    <div className={s.analysisBrowser}>
      <div className={s.analysisHeader}>
        <h3>Cost</h3>
        <button type="button" className="close-button" onClick={onClose} aria-label="Close cost dashboard">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="18" y1="6" x2="6" y2="18" />
          </svg>
        </button>
      </div>

      <div className={s.analysisMetrics}>
        <div className={s.metricsHeader}>
          <div className={s.filterBar}>
            <label className={s.filterField}>
              <span>After</span>
              <input type="date" value={after} className={!after ? s.filterDateEmpty : ''} onChange={(e) => setAfter(e.target.value)} />
            </label>
            <label className={s.filterField}>
              <span>Before</span>
              <input type="date" value={before} className={!before ? s.filterDateEmpty : ''} onChange={(e) => setBefore(e.target.value)} />
            </label>
            <label className={s.filterField}>
              <span>Agent</span>
              <select value={agent} onChange={(e) => setAgent(e.target.value)} className={s.filterSelect}>
                <option value="">All agents</option>
                {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className={s.filterField}>
              <span>Model</span>
              <select value={model} onChange={(e) => setModel(e.target.value)} className={s.filterSelect}>
                <option value="">All models</option>
                {modelOptions.map((m) => <option key={m} value={m}>{m.replace(/^[^/]+\//, '')}</option>)}
              </select>
            </label>
          </div>

          {hasData && total && (
            <div className={s.metricsSummaryCards}>
              <div className={`${s.metricsSummaryRow} ${s.metricsSummaryRowFour}`}>
                <div className={s.metricsCard}>
                  <div className={s.metricsCardValue}>{formatCost(total.cost_usd)}</div>
                  <div className={s.metricsCardLabel}>Total cost</div>
                </div>
                <div className={s.metricsCard}>
                  <div className={s.metricsCardValue}>{total.count.toLocaleString()}</div>
                  <div className={s.metricsCardLabel}>Requests</div>
                </div>
                <div className={s.metricsCard}>
                  <div className={s.metricsCardValue}>{netTokens > 0 ? netTokens.toLocaleString() : '—'}</div>
                  <div className={s.metricsCardLabel}>Net tokens</div>
                </div>
                <div className={s.metricsCard}>
                  <div className={s.metricsCardValue}>{total.input_tokens > 0 ? `${(cacheHitRate * 100).toFixed(0)}%` : '—'}</div>
                  <div className={s.metricsCardLabel}>Cache hit rate</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className={s.metricsBody}>
          {error ? <div className={s.metricsEmpty}>{error}</div> : null}
          {loading && !metrics ? <div className={s.metricsEmpty}>Loading costs…</div> : null}
          {metrics && !hasData ? <div className={s.metricsEmpty}>No cost recorded in this range.</div> : null}
          {hasData && metrics && (
            <>
              <CollapsibleSection title="Cost over time">
                <CostTrendChart points={metrics.by_day} />
              </CollapsibleSection>
              <CollapsibleSection title="Cost by agent">
                <CostDonut rows={metrics.by_agent} />
              </CollapsibleSection>
              <CollapsibleSection title="Cost by model">
                <CostBar rows={metrics.by_model} stripPrefix />
              </CollapsibleSection>
              <CollapsibleSection title="Cost by conversation">
                <CostBar rows={metrics.by_conversation} limit={15} />
              </CollapsibleSection>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
