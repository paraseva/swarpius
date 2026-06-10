import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { type UsageSnapshot } from '../hooks/useDiagnostics'
import { TokenUsagePanel } from './TokenUsagePanel'

afterEach(cleanup)

const renderPanel = (latestUsage: UsageSnapshot | null) =>
  render(<TokenUsagePanel latestUsage={latestUsage} />)

describe('TokenUsagePanel', () => {
  it('renders nothing meaningful when no usage is available', () => {
    renderPanel(null)
    expect(screen.getByText(/Waiting for usage metrics/i)).toBeInTheDocument()
  })

  it('displays per-call cost rounded to 3 decimal places when present', () => {
    renderPanel({
      call: { input_tokens: 1000, output_tokens: 200, total_tokens: 1200, cost_usd: 0.01234 },
    })
    expect(screen.getByText('Cost $0.012')).toBeInTheDocument()
  })

  it('omits cost row when call cost is zero or missing', () => {
    renderPanel({
      call: { input_tokens: 1000, output_tokens: 200, total_tokens: 1200 },
    })
    expect(screen.queryByText(/^Cost \$/)).not.toBeInTheDocument()
  })

  it('displays session cost rounded to 3 decimal places', () => {
    renderPanel({
      session_totals: { input_tokens: 5000, output_tokens: 1000, total_tokens: 6000, cost_usd: 0.12345 },
    })
    expect(screen.getByText('Cost $0.123')).toBeInTheDocument()
  })

  it('rounds up at the 4th decimal', () => {
    renderPanel({
      call: { input_tokens: 1, output_tokens: 1, total_tokens: 2, cost_usd: 0.0007 },
    })
    // 0.0007 → $0.001
    expect(screen.getByText('Cost $0.001')).toBeInTheDocument()
  })
})
