import React from 'react'
import { useWebSocket } from '../websocketContext'
import { createUuid } from '../utils/uuid'
import s from './CostDashboard.module.css'

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

const RANGES = [
  { key: '7d', label: 'Last 7 days', days: 7 },
  { key: '30d', label: 'Last 30 days', days: 30 },
  { key: '90d', label: 'Last 90 days', days: 90 },
  { key: 'all', label: 'All time', days: null as number | null },
]

// The fixed set of LLM consumers (sub-agents are off by default; they only
// appear in the data when enabled, but the filter offers them regardless).
const AGENTS = ['Coordinator', 'Diagnostic', 'Arbiter', 'Analyser']

const DAY_MS = 86_400_000

function formatCost(n: number): string {
  if (!n) return '$0.00'
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

const Card: React.FC<{ value: string; label: string }> = ({ value, label }) => (
  <div className={s.card}>
    <div className={s.cardValue}>{value}</div>
    <div className={s.cardLabel}>{label}</div>
  </div>
)

const BreakdownTable: React.FC<{ title: string; rows: GroupRow[]; limit?: number }> = ({
  title, rows, limit,
}) => {
  const shown = limit ? rows.slice(0, limit) : rows
  return (
    <section className={s.section}>
      <h3 className={s.sectionTitle}>{title}</h3>
      {shown.length === 0 ? (
        <p className={s.empty}>No data in this range.</p>
      ) : (
        <table className={s.table}>
          <thead>
            <tr><th>Name</th><th>Cost</th><th>Requests</th><th>Tokens</th></tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.key ?? '—'}>
                <td className={s.nameCell}>{r.key ?? '—'}</td>
                <td className={s.numCell}>{formatCost(r.cost_usd)}</td>
                <td className={s.numCell}>{r.count}</td>
                <td className={s.numCell}>{formatTokens(r.input_tokens + r.output_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {limit && rows.length > limit ? (
        <p className={s.more}>+{rows.length - limit} more</p>
      ) : null}
    </section>
  )
}

const DayTrend: React.FC<{ rows: GroupRow[] }> = ({ rows }) => {
  const max = rows.reduce((m, r) => Math.max(m, r.cost_usd), 0)
  return (
    <section className={s.section}>
      <h3 className={s.sectionTitle}>Cost per day</h3>
      {rows.length === 0 ? (
        <p className={s.empty}>No data in this range.</p>
      ) : (
        <div className={s.trend}>
          {rows.map((r) => (
            <div key={r.key ?? '—'} className={s.trendRow}>
              <span className={s.trendDay}>{r.key}</span>
              <span className={s.trendBarTrack}>
                <span
                  className={s.trendBar}
                  style={{ width: max > 0 ? `${(r.cost_usd / max) * 100}%` : '0%' }}
                />
              </span>
              <span className={s.trendCost}>{formatCost(r.cost_usd)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export const CostDashboard: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { messages, sendMessage } = useWebSocket()
  const [rangeKey, setRangeKey] = React.useState('30d')
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
    const range = RANGES.find((r) => r.key === rangeKey)
    const payload: Record<string, unknown> = { request_id: rid }
    if (range && range.days != null) payload.since_ms = Date.now() - range.days * DAY_MS
    if (agent) payload.agent = agent
    if (model) payload.model = model
    sendMessage('cost-metrics-request', JSON.stringify(payload))
  }, [sendMessage, rangeKey, agent, model])

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
      // Keep the model filter options stable: refresh them only from an
      // unfiltered (all-models) response.
      if (!model) {
        setModelOptions(payload.by_model.map((r) => r.key).filter((k): k is string => !!k))
      }
    }
  }, [messages, model])

  return (
    <div className={s.dashboard}>
      <header className={s.header}>
        <h2 className={s.title}>Cost</h2>
        <button type="button" className={s.closeButton} onClick={onClose} aria-label="Close cost dashboard">
          ×
        </button>
      </header>

      <div className={s.filterBar}>
        <select className={s.select} value={rangeKey} onChange={(e) => setRangeKey(e.target.value)} aria-label="Time range">
          {RANGES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
        <select className={s.select} value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent filter">
          <option value="">All agents</option>
          {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select className={s.select} value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model filter">
          <option value="">All models</option>
          {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      <div className={s.body}>
        {error ? <p className={s.error} role="alert">{error}</p> : null}
        {loading && !metrics ? <p className={s.empty}>Loading costs…</p> : null}
        {metrics ? (
          <>
            <div className={s.cards}>
              <Card value={formatCost(metrics.total.cost_usd)} label="Total cost" />
              <Card value={formatTokens(metrics.total.input_tokens + metrics.total.output_tokens)} label="Tokens" />
              <Card value={String(metrics.total.count)} label="Requests" />
            </div>
            <DayTrend rows={metrics.by_day} />
            <BreakdownTable title="By agent" rows={metrics.by_agent} />
            <BreakdownTable title="By model" rows={metrics.by_model} />
            <BreakdownTable title="By conversation" rows={metrics.by_conversation} limit={15} />
          </>
        ) : null}
      </div>
    </div>
  )
}
