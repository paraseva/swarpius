import React from 'react'
import s from './LlmDiagnosticsPanel.module.css'
import { RequestIdBadge } from './RequestIdBadge'
import { parseJson } from '../utils/parseJson'
import { useWebSocket } from '../websocketContext'

interface LlmDiagnosticEvent {
  event_type?: string
  agent_name?: string
  model?: string
  call_id?: string
  request_id?: string
  prompt_tokens?: number
  prompt_tokens_source?: string
  duration_ms?: number
  confidence?: number
  reason?: string
  action?: string
  decision_source?: string
  timestamp_ms?: number
  error?: string
  conversation_id?: string
  topic_summary?: string
  is_new?: boolean
  prompt_diagnostics?: {
    estimated_input_tokens?: number
    input_schema_tokens_estimated?: number
    system_prompt_tokens_estimated?: number
    context_tokens_estimated?: number
    context_breakdown?: Array<{
      name?: string
      estimated_tokens?: number
      char_count?: number
    }>
  }
}

interface AgentOutputEvent {
  event_type?: string
  request_id?: string
}

export const LlmDiagnosticsPanel: React.FC = () => {
  const { messages } = useWebSocket()

  const diagnostics = React.useMemo(() => {
    type CallInfo = {
      callId: string
      agentName: string
      model?: string
      promptTokens: number
      source: string
      startedAtMs: number
      promptDiagnostics: LlmDiagnosticEvent['prompt_diagnostics'] | null
    }
    const activeCalls = new Map<string, CallInfo>()
    let latestFailure: { agentName: string; error: string } | null = null
    let latestInterrupt: LlmDiagnosticEvent | null = null
    let currentRequestId: string | null = null
    let currentConversation: { id: string; topic: string } | null = null

    for (const message of messages) {
      if (message.direction !== 'inbound') continue

      if (message.channel === 'llm-diagnostics') {
        const event = parseJson<LlmDiagnosticEvent>(message.payload ?? message.body)
        if (!event) continue
        const fallbackTs = message.timestamp

        if (event.request_id) {
          currentRequestId = event.request_id
        }
        if (event.event_type === 'call_started' && event.call_id) {
          activeCalls.set(event.call_id, {
            callId: event.call_id,
            agentName: event.agent_name ?? 'Unknown Agent',
            model: event.model,
            promptTokens: event.prompt_tokens ?? 0,
            source: event.prompt_tokens_source ?? 'unknown',
            startedAtMs: event.timestamp_ms ?? fallbackTs,
            promptDiagnostics: event.prompt_diagnostics ?? null,
          })
          latestFailure = null
        } else if (event.event_type === 'call_completed' && event.call_id) {
          activeCalls.delete(event.call_id)
        } else if (event.event_type === 'call_failed' && event.call_id) {
          const info = activeCalls.get(event.call_id)
          activeCalls.delete(event.call_id)
          latestFailure = {
            agentName: info?.agentName ?? 'Unknown Agent',
            error: event.error ?? 'Call failed',
          }
        } else if (event.event_type === 'interrupt_decision') {
          latestInterrupt = event
        } else if (event.event_type === 'conversation_assigned') {
          currentConversation = {
            id: event.conversation_id ?? '',
            topic: event.topic_summary ?? '',
          }
        }
      }

      if (message.channel === 'agent-outputs') {
        const event = parseJson<AgentOutputEvent>(message.payload ?? message.body)
        if (event?.request_id) {
          currentRequestId = event.request_id
        }
      }
    }

    let activeCall: CallInfo | null = null
    for (const info of activeCalls.values()) {
      activeCall = info
    }
    const activeCallPromptDiagnostics = activeCall?.promptDiagnostics ?? null

    return {
      activeCall,
      activeCallPromptDiagnostics,
      latestInterrupt,
      currentRequestId,
      currentConversation,
      latestFailure: activeCall ? null : latestFailure,
    }
  }, [messages])

  return (
    <div className={`panel panel-history ${s.panel}`}>
      <div className="panel-header">
        <h3>LLM Calls</h3>
        {diagnostics.currentConversation ? (
          <span className={s.conversation} title={diagnostics.currentConversation.topic}>
            {diagnostics.currentConversation.id}{diagnostics.currentConversation.topic ? `: ${diagnostics.currentConversation.topic}` : ''}
          </span>
        ) : null}
        {diagnostics.currentRequestId ? (
          <span className={s.requestId}>
            <RequestIdBadge requestId={diagnostics.currentRequestId} />
          </span>
        ) : null}
      </div>
      <div className="panel-body scrollable">
        <div className={s.section}>
          <div className={s.row}>
            <span className={`${s.statusDot} ${diagnostics.activeCall ? s.statusDotActive : s.statusDotIdle}`} aria-label={diagnostics.activeCall ? 'Active' : 'Idle'} role="status" />
            <strong title="Real-time status of the current LLM call — prompt composition and context provider breakdown while active">{diagnostics.activeCall ? 'LLM call in progress' : 'No active LLM call'}</strong>
            {diagnostics.activeCall ? (
              <ElapsedSince startedAtMs={diagnostics.activeCall.startedAtMs} />
            ) : null}
          </div>
          <div className={s.meta}>
            {diagnostics.activeCall ? (
              <>
                <span>Agent: {diagnostics.activeCall.agentName}</span>
                {diagnostics.activeCall.model && <span>Model: {diagnostics.activeCall.model}</span>}
                <span>Prompt tokens: {diagnostics.activeCall.promptTokens.toLocaleString()}</span>
                <span>Source: {diagnostics.activeCall.source}</span>
              </>
            ) : diagnostics.latestFailure ? (
              <span>
                Last call failed ({diagnostics.latestFailure.agentName}):{' '}
                <span className={s.errorText}>{diagnostics.latestFailure.error}</span>
              </span>
            ) : (
              <span>Waiting for the next model invocation.</span>
            )}
          </div>
          {diagnostics.activeCall && diagnostics.activeCallPromptDiagnostics ? (
            <div className={s.meta}>
              <span>
                System {(
                  diagnostics.activeCallPromptDiagnostics.system_prompt_tokens_estimated ?? 0
                ).toLocaleString()}
              </span>
              <span>
                Context {(diagnostics.activeCallPromptDiagnostics.context_tokens_estimated ?? 0).toLocaleString()}
              </span>
              <span>
                Input {(diagnostics.activeCallPromptDiagnostics.input_schema_tokens_estimated ?? 0).toLocaleString()}
              </span>
            </div>
          ) : null}
          {diagnostics.activeCallPromptDiagnostics?.context_breakdown?.length ? (
            <div className={s.meta}>
              <span>
                Top context: {diagnostics.activeCallPromptDiagnostics.context_breakdown
                  .slice(0, 3)
                  .map((item) => `${item.name ?? 'unknown'}:${(item.estimated_tokens ?? 0).toLocaleString()}`)
                  .join(' | ')}
              </span>
            </div>
          ) : null}
        </div>

        <div className={s.section}>
          <strong title="Most recent interrupt decision — from the arbiter (queue / interrupt) or a keyword directive (stop, cancel)">Last Interrupt Decision</strong>
          {diagnostics.latestInterrupt ? (
            <div className={s.interruptGrid}>
              <span className={s.interruptLabel}>Source</span>
              <span className={s.interruptValue}>
                <span className={`${s.interruptBadge} ${decisionBadgeClass(diagnostics.latestInterrupt.decision_source, s)}`}>
                  {formatDecisionSource(diagnostics.latestInterrupt.decision_source)}
                </span>
              </span>
              <span className={s.interruptLabel}>Action</span>
              <span className={s.interruptValue}>
                <span className={s.interruptActionPill}>
                  {diagnostics.latestInterrupt.action ?? 'unknown'}
                </span>
                {typeof diagnostics.latestInterrupt.confidence === 'number' ? (
                  <span style={{ marginLeft: '0.4rem', color: 'var(--color-text-secondary)', fontSize: '0.72rem' }}>
                    confidence {diagnostics.latestInterrupt.confidence.toFixed(2)}
                  </span>
                ) : null}
              </span>
              <span className={s.interruptLabel}>Reason</span>
              <span className={s.interruptReason}>
                {diagnostics.latestInterrupt.reason ?? 'n/a'}
              </span>
            </div>
          ) : (
            <p className="empty-placeholder">No interrupt decisions yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function formatDecisionSource(src?: string): string {
  switch (src) {
    case 'arbiter': return 'Arbiter'
    case 'arbiter_fallback': return 'Arbiter (fallback)'
    case 'keyword': return 'Keyword directive'
    default: return src ?? 'unknown'
  }
}

function decisionBadgeClass(src: string | undefined, s: Record<string, string>): string {
  switch (src) {
    case 'arbiter': return s.interruptBadgeArbiter ?? ''
    case 'arbiter_fallback': return s.interruptBadgeArbiterFallback ?? ''
    case 'keyword': return s.interruptBadgeKeyword ?? ''
    default: return ''
  }
}

const ElapsedSince: React.FC<{ startedAtMs: number }> = ({ startedAtMs }) => {
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const elapsed = Math.max(0, Math.floor((now - startedAtMs) / 1000))
  const label = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
  return <span className="elapsed-since" aria-label="elapsed">{label}</span>
}
