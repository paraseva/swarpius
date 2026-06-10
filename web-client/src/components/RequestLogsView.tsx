import React from 'react'
import { parseDetailsMarkup } from '../utils/parseDetailsMarkup'
import { JsonTreeView } from './JsonTreeView'
import { Chevron, PromptViewer } from './PromptViewer'
import {
  formatTokenCount,
  type CoordinatorStep,
  type RequestLogs,
  type ToolExecution,
} from './AnalysisBrowser.shared'
import s from './AnalysisBrowser.module.css'

const ToolExecutionCard: React.FC<{ exec: ToolExecution }> = ({ exec }) => {
  const [showOutput, setShowOutput] = React.useState(false)

  return (
    <div className={s.rqLogTool}>
      <button
        type="button"
        className={s.rqLogToolHeader}
        onClick={() => setShowOutput(!showOutput)}
      >
        <span className={s.rqLogToolStep}>Step {exec.step}</span>
        <span className={s.rqLogToolSkill}>{exec.selected_skill}</span>
        {exec.attempt != null && exec.attempt > 1 && (
          <span className={s.rqLogToolRetry}>
            {exec.attempt - 1} {exec.attempt - 1 === 1 ? 'retry' : 'retries'}
          </span>
        )}
        <span className={s.rqLogToolDuration}>{exec.duration_ms}ms</span>
        {exec.error && <span className={s.rqLogToolError}>error</span>}
        <Chevron expanded={showOutput} />
      </button>
      {showOutput && (
        <div className={s.rqLogToolBody}>
          <div className={s.rqLogToolSection}>
            <div className={s.rqLogToolLabel}>Input</div>
            <JsonTreeView data={exec.tool_input} className="rq-log-tree" />
          </div>
          {exec.tool_output != null && (
            <div className={s.rqLogToolSection}>
              <div className={s.rqLogToolLabel}>Output</div>
              <JsonTreeView data={exec.tool_output} className="rq-log-tree" />
            </div>
          )}
          {exec.error && (
            <div className={s.rqLogToolSection}>
              <div className={`${s.rqLogToolLabel} ${s.rqLogToolError}`}>Error</div>
              <pre className={s.rqLogPre}>{exec.error}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const CoordinatorStepCard: React.FC<{ step: CoordinatorStep }> = ({ step }) => {
  const [showInput, setShowInput] = React.useState(false)
  const output = step.coordinator_output as Record<string, unknown> | undefined
  const action = output?.action as string | undefined
  const toolCalls = output?.tool_calls as Array<Record<string, unknown>> | undefined
  const responseText = output?.text as string | undefined
  const usage = step.usage

  return (
    <div className={s.rqLogTool}>
      <button
        type="button"
        className={s.rqLogToolHeader}
        onClick={() => setShowInput(!showInput)}
      >
        <span className={s.rqLogToolStep}>Step {step.step}</span>
        <span className={s.rqLogToolSkill}>
          {action === 'tool_call' && toolCalls
            ? toolCalls.map((tc) => tc.tool as string).join(', ')
            : action === 'text_response'
              ? 'text response'
              : action ?? '?'}
        </span>
        {(usage?.cost_usd ?? 0) > 0 && (
          <span className={s.rqLogToolDuration}>${(usage!.cost_usd!).toFixed(3)}</span>
        )}
        {step.duration_ms != null && (
          <span className={s.rqLogToolDuration}>
            {(usage?.cost_usd ?? 0) > 0 ? '· ' : ''}{step.duration_ms}ms
          </span>
        )}
        <Chevron expanded={showInput} />
      </button>
      {showInput && (
        <div className={s.rqLogToolBody}>
          {action === 'tool_call' && toolCalls && (
            <div className={s.rqLogToolSection}>
              <div className={s.rqLogToolLabel}>Tool calls</div>
              <JsonTreeView data={toolCalls} className="rq-log-tree" />
            </div>
          )}
          {action === 'text_response' && responseText && (
            <div className={s.rqLogToolSection}>
              <div className={s.rqLogToolLabel}>Response</div>
              <pre className={s.rqLogPre}>{responseText}</pre>
            </div>
          )}
          <div className={s.rqLogToolSection}>
            <div className={s.rqLogToolLabel}>Coordinator input</div>
            <JsonTreeView data={step.coordinator_input} className="rq-log-tree" />
          </div>
          {usage && (
            <div className={s.rqLogToolSection}>
              <div className={s.rqLogToolLabel}>Token usage</div>
              <div className={s.rqLogKvRow}>
                <span>{formatTokenCount((usage.input_tokens ?? 0) - (usage.cache_read_input_tokens ?? 0))} in</span>
                <span>{formatTokenCount(usage.output_tokens ?? 0)} out</span>
                {(usage.cache_read_input_tokens ?? 0) > 0 && (
                  <span>+{formatTokenCount(usage.cache_read_input_tokens!)} cached</span>
                )}
                {(usage.cache_creation_input_tokens ?? 0) > 0 && (
                  <span>{formatTokenCount(usage.cache_creation_input_tokens!)} cache write</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const DetailsSection: React.FC<{ content: string; summary?: string }> = ({ content, summary }) => {
  const [collapsed, setCollapsed] = React.useState(false)
  return (
    <div className="detailed-info-section">
      <button
        type="button"
        className="detailed-info-toggle"
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <svg className="detailed-info-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
        {summary || 'Details'}
      </button>
      {!collapsed && (
        <div className="detailed-info-content">
          <pre className="message-pre">{content}</pre>
        </div>
      )}
    </div>
  )
}

export const RequestLogsView: React.FC<{ logs: RequestLogs }> = ({ logs }) => {
  const req = logs.request
  const outcome = logs.outcome

  return (
    <div className={s.rqLogViewer}>
      {req && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Request</div>
          <div className={s.rqLogKv}>
            <span className={s.rqLogKey}>Input:</span>
            <span className={s.rqLogValue}>{req.user_input ?? '—'}</span>
          </div>
        </div>
      )}
      {outcome && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Outcome</div>
          <div className={s.rqLogKvRow}>
            <span>{outcome.status ?? '—'}</span>
            <span>{outcome.total_steps ?? '?'} steps</span>
            <span>{outcome.total_duration_ms != null ? `${outcome.total_duration_ms}ms` : '—'}</span>
          </div>
          {outcome.chat_response && (() => {
            const segments = parseDetailsMarkup(outcome.chat_response)
            return segments.map((seg, i) =>
              seg.type === 'extended_info' ? (
                <DetailsSection key={i} content={seg.content} summary={seg.summary} />
              ) : (
                <div key={i} className={s.rqLogResponse}>
                  <pre className={s.rqLogPre}>{seg.content}</pre>
                </div>
              ),
            )
          })()}
        </div>
      )}
      {outcome?.usage && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Token Usage</div>
          <div className={s.rqLogKvRow}>
            <span>{formatTokenCount((outcome.usage.input_tokens ?? 0) - (outcome.usage.cache_read_input_tokens ?? 0))} in</span>
            <span>{formatTokenCount(outcome.usage.output_tokens ?? 0)} out</span>
            {(outcome.usage.cache_read_input_tokens ?? 0) > 0 && (
              <span>+{formatTokenCount(outcome.usage.cache_read_input_tokens!)} cached</span>
            )}
            {(outcome.usage.cache_creation_input_tokens ?? 0) > 0 && (
              <span>{formatTokenCount(outcome.usage.cache_creation_input_tokens!)} cache write</span>
            )}
            {(outcome.usage.cost_usd ?? 0) > 0 && (
              <span>${(outcome.usage.cost_usd!).toFixed(3)}</span>
            )}
          </div>
        </div>
      )}
      {logs.prompts && Object.keys(logs.prompts).length > 0 && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>System Prompts ({Object.keys(logs.prompts).length})</div>
          {Object.entries(logs.prompts).map(([name, content]) => (
            <PromptViewer key={name} name={name} content={content} />
          ))}
        </div>
      )}
      {logs.coordinator_steps && logs.coordinator_steps.length > 0 && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Coordinator Steps ({logs.coordinator_steps.length})</div>
          {logs.coordinator_steps.map((step, i) => (
            <CoordinatorStepCard key={i} step={step} />
          ))}
        </div>
      )}
      {logs.tool_executions.length > 0 && (
        <div className={s.rqLogSection}>
          <div className={s.rqLogSectionTitle}>Tool Executions ({logs.tool_executions.length})</div>
          {logs.tool_executions.map((exec, i) => (
            <ToolExecutionCard key={i} exec={exec} />
          ))}
        </div>
      )}
    </div>
  )
}
