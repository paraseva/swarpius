import s from './TokenUsagePanel.module.css'
import { type UsageSnapshot } from '../hooks/useDiagnostics'

const formatTokens = (value?: number) => (typeof value === 'number' ? value.toLocaleString() : '0')
const formatCost = (value?: number) => `$${(value ?? 0).toFixed(3)}`

interface TokenUsagePanelProps {
  latestUsage: UsageSnapshot | null
}

export const TokenUsagePanel: React.FC<TokenUsagePanelProps> = ({ latestUsage }) => (
  <div className={s.panel}>
    <div className={s.grid}>
      <section className={s.card}>
        <div className={s.label} title="Token consumption for the most recent LLM call">
          Last Call
        </div>
        <strong className={s.total}>{formatTokens((latestUsage?.call?.total_tokens ?? 0) - (latestUsage?.call?.cache_read_input_tokens ?? 0))}</strong>
        <div className={s.breakdown}>
          <span>In {formatTokens((latestUsage?.call?.input_tokens ?? 0) - (latestUsage?.call?.cache_read_input_tokens ?? 0))}</span>
          <span>Out {formatTokens(latestUsage?.call?.output_tokens)}</span>
        </div>
        {(latestUsage?.call?.cache_read_input_tokens ?? 0) > 0 ? (
          <div className={`${s.breakdown} token-cached`}>
            <span>+{formatTokens(latestUsage?.call?.cache_read_input_tokens)} cached</span>
          </div>
        ) : null}
        {(latestUsage?.call?.cost_usd ?? 0) > 0 ? (
          <div className={s.breakdown}>
            <span>Cost {formatCost(latestUsage?.call?.cost_usd)}</span>
          </div>
        ) : null}
      </section>
      <section className={s.card}>
        <div className={s.label} title="Rolling one-minute token consumption and request throughput">Per Minute</div>
        <strong className={s.total}>
          {formatTokens(latestUsage?.tokens_per_minute?.total_tokens)}
        </strong>
        <div className={s.breakdown}>
          <span>In {formatTokens(latestUsage?.tokens_per_minute?.input_tokens)}</span>
          <span>Out {formatTokens(latestUsage?.tokens_per_minute?.output_tokens)}</span>
        </div>
        <div className={s.breakdown}>
          <span>Req {formatTokens(latestUsage?.requests_per_minute?.request_count)}</span>
          <span>/ {formatTokens(latestUsage?.requests_per_minute?.window_seconds)}s</span>
        </div>
        <div className={s.breakdown}>
          <span>
            Retry est {formatTokens(latestUsage?.tokens_per_minute_breakdown?.rate_limited_retry_total_tokens_estimated)}
          </span>
          <span>Source {latestUsage?.source ?? 'n/a'}</span>
        </div>
      </section>
      <section className={s.card}>
        <div className={s.label} title="Total token usage since the agent server was started">Server</div>
        <strong className={s.total}>
          {formatTokens((latestUsage?.session_totals?.total_tokens ?? 0) - (latestUsage?.session_totals?.cache_read_input_tokens ?? 0))}
        </strong>
        <div className={s.breakdown}>
          <span>In {formatTokens((latestUsage?.session_totals?.input_tokens ?? 0) - (latestUsage?.session_totals?.cache_read_input_tokens ?? 0))}</span>
          <span>Out {formatTokens(latestUsage?.session_totals?.output_tokens)}</span>
        </div>
        <div className={s.breakdown}>
          <span>
            Retry est {formatTokens(latestUsage?.session_breakdown?.rate_limited_retry_total_tokens_estimated)}
          </span>
          <span>
            Success {formatTokens(latestUsage?.session_breakdown?.success_total_tokens)}
          </span>
        </div>
        {(latestUsage?.session_totals?.cache_read_input_tokens ?? 0) > 0 ? (
          <div className={`${s.breakdown} token-cached`}>
            <span>
              +{formatTokens(latestUsage?.session_totals?.cache_read_input_tokens)} cached
            </span>
            <span>
              Cache write {formatTokens(latestUsage?.session_totals?.cache_creation_input_tokens)}
            </span>
          </div>
        ) : null}
        {(latestUsage?.session_totals?.cost_usd ?? 0) > 0 ? (
          <div className={s.breakdown}>
            <span>Cost {formatCost(latestUsage?.session_totals?.cost_usd)}</span>
          </div>
        ) : null}
      </section>
    </div>
    {!latestUsage ? (
      <div className={s.empty}>Waiting for usage metrics from the backend...</div>
    ) : null}
  </div>
)
