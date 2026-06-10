import React from 'react'
import s from './PromptBudgetPanel.module.css'
import { parseJson } from '../utils/parseJson'
import { useWebSocket } from '../websocketContext'

interface ContextBreakdownItem {
  name?: string
  estimated_tokens?: number
}

interface PromptDiagnosticsPayload {
  estimated_input_tokens?: number
  input_schema_tokens_estimated?: number
  system_prompt_tokens_estimated?: number
  context_tokens_estimated?: number
  context_breakdown?: ContextBreakdownItem[]
}

interface LlmDiagnosticEvent {
  event_type?: string
  timestamp_ms?: number
  agent_name?: string
  prompt_diagnostics?: PromptDiagnosticsPayload
}

interface PromptSnapshot {
  total: number
  system: number
  context: number
  input: number
  providers: Map<string, number>
  agentName: string
}

interface PromptServerSummary {
  calls: number
  total: number
  system: number
  context: number
  input: number
  providers: Map<string, number>
}

const fmt = (n: number) => n.toLocaleString()

const buildSnapshot = (diagnostics: PromptDiagnosticsPayload, agentName: string): PromptSnapshot => {
  const providers = new Map<string, number>()
  for (const item of diagnostics.context_breakdown ?? []) {
    const name = (item.name || 'unknown').trim() || 'unknown'
    providers.set(name, Number(item.estimated_tokens ?? 0))
  }
  return {
    total: Number(diagnostics.estimated_input_tokens ?? 0),
    system: Number(diagnostics.system_prompt_tokens_estimated ?? 0),
    context: Number(diagnostics.context_tokens_estimated ?? 0),
    input: Number(diagnostics.input_schema_tokens_estimated ?? 0),
    providers,
    agentName,
  }
}

const buildServerSummary = (events: Array<{ diagnostics: PromptDiagnosticsPayload }>): PromptServerSummary => {
  let total = 0
  let system = 0
  let context = 0
  let input = 0
  const providers = new Map<string, number>()

  for (const event of events) {
    const d = event.diagnostics
    total += Number(d.estimated_input_tokens ?? 0)
    system += Number(d.system_prompt_tokens_estimated ?? 0)
    context += Number(d.context_tokens_estimated ?? 0)
    input += Number(d.input_schema_tokens_estimated ?? 0)
    for (const item of d.context_breakdown ?? []) {
      const name = (item.name || 'unknown').trim() || 'unknown'
      const tokens = Number(item.estimated_tokens ?? 0)
      providers.set(name, (providers.get(name) ?? 0) + tokens)
    }
  }

  return { calls: events.length, total, system, context, input, providers }
}

/** Merge provider names from both maps, sorted by server total descending. */
const mergedProviderNames = (lastCall: Map<string, number>, server: Map<string, number>): string[] => {
  const all = new Set([...server.keys(), ...lastCall.keys()])
  return [...all].sort((a, b) => (server.get(b) ?? 0) - (server.get(a) ?? 0))
}

export const PromptBudgetPanel: React.FC = () => {
  const { messages } = useWebSocket()

  const budget = React.useMemo(() => {
    const allEvents: Array<{ diagnostics: PromptDiagnosticsPayload; agentName: string }> = []

    for (const message of messages) {
      if (message.direction !== 'inbound' || message.channel !== 'llm-diagnostics') continue
      const parsed = parseJson<LlmDiagnosticEvent>(message.payload ?? message.body)
      if (!parsed || parsed.event_type !== 'call_started' || !parsed.prompt_diagnostics) continue
      allEvents.push({
        diagnostics: parsed.prompt_diagnostics,
        agentName: parsed.agent_name ?? 'Unknown',
      })
    }

    const last = allEvents.length > 0 ? allEvents[allEvents.length - 1] : null
    const lastCall = last ? buildSnapshot(last.diagnostics, last.agentName) : null
    const server = buildServerSummary(allEvents)

    return { lastCall, server }
  }, [messages])

  const providerNames = React.useMemo(() => {
    if (!budget.lastCall) return [...budget.server.providers.keys()].sort((a, b) => (budget.server.providers.get(b) ?? 0) - (budget.server.providers.get(a) ?? 0))
    return mergedProviderNames(budget.lastCall.providers, budget.server.providers)
  }, [budget])

  const hasData = budget.lastCall || budget.server.calls > 0

  return (
    <div className="panel panel-history prompt-budget-panel">
      <div className="panel-header">
        <h3>Prompt Budget</h3>
      </div>
      <div className="panel-body scrollable">
        {!hasData ? (
          <p className="empty-placeholder">No prompt diagnostics captured yet.</p>
        ) : (
          <table className={s.table}>
            <thead>
              <tr>
                <th></th>
                <th title="Prompt token breakdown for the most recent LLM call">Last Call</th>
                <th title="Cumulative prompt token breakdown since the agent server was started">Server</th>
              </tr>
            </thead>
            <tbody>
              {budget.lastCall && (
                <tr>
                  <td>Agent</td>
                  <td>{budget.lastCall.agentName}</td>
                  <td></td>
                </tr>
              )}
              <tr>
                <td>Calls</td>
                <td></td>
                <td>{fmt(budget.server.calls)}</td>
              </tr>
              <tr className={s.rowBold}>
                <td>Total</td>
                <td>{budget.lastCall ? fmt(budget.lastCall.total) : ''}</td>
                <td>{fmt(budget.server.total)}</td>
              </tr>
              <tr className={s.rowIndent}>
                <td>System</td>
                <td>{budget.lastCall ? fmt(budget.lastCall.system) : ''}</td>
                <td>{fmt(budget.server.system)}</td>
              </tr>
              <tr className={s.rowIndent}>
                <td>Context</td>
                <td>{budget.lastCall ? fmt(budget.lastCall.context) : ''}</td>
                <td>{fmt(budget.server.context)}</td>
              </tr>
              <tr className={s.rowIndent}>
                <td>Input schema</td>
                <td>{budget.lastCall ? fmt(budget.lastCall.input) : ''}</td>
                <td>{fmt(budget.server.input)}</td>
              </tr>
              {providerNames.length > 0 && (
                <>
                  <tr className={s.rowSeparator}>
                    <td colSpan={3}>Context breakdown</td>
                  </tr>
                  {providerNames.map((name) => (
                    <tr key={name} className={s.rowIndent}>
                      <td>{name}</td>
                      <td>{budget.lastCall?.providers.get(name) != null ? fmt(budget.lastCall.providers.get(name)!) : ''}</td>
                      <td>{budget.server.providers.get(name) != null ? fmt(budget.server.providers.get(name)!) : ''}</td>
                    </tr>
                  ))}
                </>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
