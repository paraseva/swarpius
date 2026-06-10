/**
 * Shared types, constants, and helpers for AnalysisBrowser and its
 * sub-components. Lives in its own file so sibling components can
 * import them without a circular dependency on AnalysisBrowser.tsx.
 */

import s from './AnalysisBrowser.module.css'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AnalysisListEntry {
  date: string
  conversation_id: string
  first_request_at?: string
  topic: string
  requests_analysed: number
  total_steps: number
  avg_steps_per_request: number
  total_tool_calls: number
  total_cost_usd?: number
  git_ref: string
  finding_count: number
  severity_summary: Record<string, number>
  analysed_at: string
  feedback_count?: number
  pending_feedback?: number
  analysis_revisions?: number
  coordinator_model?: string
}

export interface AnalysisFinding {
  id?: string
  request_id: string
  failure_mode: string
  failure_name: string
  severity: string
  summary: string
  detail: string
}

export interface RevokedFinding {
  id?: string
  reason: string
  // Populated by analyse.py's _apply_revocations when the revocation
  // matched a finding. Unmatched revocations are dropped before the
  // analysis is persisted, so this should always be present in
  // production data — typed optional for defensive rendering.
  original_finding?: AnalysisFinding
}

export interface ToolExecution {
  step: number
  selected_skill: string
  tool_input: Record<string, unknown>
  tool_output: unknown
  duration_ms: number
  attempt?: number
  retry_notes?: string | null
  error?: string | null
}

export interface TokenUsage {
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
  cost_usd?: number
}

export interface CoordinatorStep {
  request_id?: string
  step: number
  coordinator_input: unknown
  coordinator_output: unknown
  context_providers?: unknown
  duration_ms?: number
  usage?: TokenUsage
}

export interface RequestLogs {
  request_id: string
  request?: { user_input?: string; timestamp?: string }
  outcome?: { status?: string; chat_response?: string; total_steps?: number; total_duration_ms?: number; usage?: TokenUsage }
  tool_executions: ToolExecution[]
  coordinator_steps?: CoordinatorStep[]
  prompts?: Record<string, string>
}

export interface RequestSummary {
  request_id: string
  user_input?: string
  timestamp?: string
  coordinator_model?: string
  status?: string
  total_steps?: number
  total_duration_ms?: number
  usage?: TokenUsage
}

export interface RequestLogsResponse {
  request_id?: string
  ok: boolean
  logs?: RequestLogs | null
  error?: string
}

export interface ResultHandleData {
  result_handle: string
  search_history_line: string | null
  items: string[] | null
  source_request_id: string | null
}

export interface ResultHandleResponse {
  request_id?: string
  ok: boolean
  data?: ResultHandleData | null
  error?: string
}

export interface FeedbackItem {
  // Identity — stable across re-analyses.
  request_id: string
  failure_mode: string
  disposition: string
  rebuttal: string
  timestamp: string
  lesson_status: string
  validation_iterations: number
}

export function feedbackKey(item: Pick<FeedbackItem, 'request_id' | 'failure_mode'>): string {
  return `${item.request_id}::${item.failure_mode}`
}

export interface FeedbackResponse {
  request_id?: string
  ok: boolean
  items?: FeedbackItem[]
  item?: FeedbackItem
  error?: string
}

export interface AnalysisHistoryEntry {
  analysed_at: string
  git_ref: string
  conversation_id: string
  date: string
  topic: string
  requests_analysed: number
  total_tool_calls: number
  total_steps: number
  avg_steps_per_request: number
  findings: AnalysisFinding[]
  revoked_findings?: RevokedFinding[]
  notes: string
  feedback: FeedbackItem[]
  superseded_at: string
}

export interface AnalysisDetail {
  analysed_at: string
  git_ref: string
  conversation_id: string
  date: string
  topic: string
  requests_analysed: number
  total_tool_calls: number
  total_steps: number
  avg_steps_per_request: number
  findings: AnalysisFinding[]
  revoked_findings?: RevokedFinding[]
  notes: string
  history?: AnalysisHistoryEntry[]
  requests?: RequestSummary[]
}

export interface ListResponse {
  request_id?: string
  ok: boolean
  conversations?: AnalysisListEntry[]
  models?: string[]
  error?: string
}

export interface DetailResponse {
  request_id?: string
  ok: boolean
  analysis?: AnalysisDetail | null
  error?: string
}

export interface RunResponse {
  request_id?: string
  ok?: boolean
  accepted?: boolean
  completed?: boolean
  analysis?: AnalysisDetail | null
  analysed_count?: number
  errors?: string[]
  error?: string
}

export interface MetricsUsage {
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
  cost_usd?: number
}

export interface MetricsEntry {
  conversation_id: string
  date: string
  git_ref: string
  coordinator_model?: string
  finding_count: number
  findings_by_mode: Record<string, number>
  findings_by_severity: Record<string, number>
  revoked_count?: number
  revoked_by_mode?: Record<string, number>
  avg_steps: number
  requests: number
  steps: number
  analysed_at?: string
  usage?: MetricsUsage
}

export interface MetricsResponse {
  request_id?: string
  ok: boolean
  total_conversations?: number
  total_findings?: number
  total_revoked?: number
  findings_by_severity?: Record<string, number>
  findings_by_mode?: Record<string, number>
  revoked_by_mode?: Record<string, number>
  avg_steps_per_request?: number
  git_refs?: string[]
  models?: string[]
  total_input_tokens?: number
  total_output_tokens?: number
  total_cache_read_tokens?: number
  total_cache_creation_tokens?: number
  total_cost_usd?: number
  cache_hit_rate?: number
  entries?: MetricsEntry[]
  error?: string
}

export interface AnalysisUpdatePayload {
  type: string
  date?: string
  conversation_id?: string
  conversations?: AnalysisListEntry[]
  entry?: AnalysisListEntry
}

export type AnalysisSubTab = 'conversations' | 'metrics'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const POLL_INTERVAL_MS = 30_000

export const FAILURE_MODE_DESCRIPTIONS: Record<string, string> = {
  'FM-01': 'Unnecessary tool call — tool called that wasn\'t needed',
  'FM-02': 'Wrong tool selection — incorrect tool for the task',
  'FM-03': 'Wrong tool ordering — right tools, wrong sequence',
  'FM-04': 'Premature termination — responded before completing all parts',
  'FM-05': 'Incorrect search parameters — wrong query, category, or operation',
  'FM-06': 'Incorrect action parameters — wrong action type, zone, or reference',
  'FM-07': 'Item type mismatch — track/album confusion',
  'FM-08': 'Failed context reference — didn\'t use history or cache',
  'FM-09': 'Wrong result reference — used wrong item from results',
  'FM-10': 'Follow-up misinterpretation — misread user intent',
  'FM-11': 'Confabulation — fabricated information not in tool results',
  'FM-12': 'Excessive steps — significantly more steps than expected',
  'FM-13': 'Looping — same tool call repeated without progress',
  'FM-14': 'Over-completion — did more than asked',
  'FM-15': 'Poor error recovery — didn\'t handle errors gracefully',
  'FM-16': 'Unhelpful response — too verbose, unclear, or missing confirmation',
  'FM-17': 'Inaccurate response — contradicts what actually happened',
  'FM-18': 'Interrupt handling failure — interrupt not cleanly processed',
  'FM-19': 'Conversation grouping inconsistency — requests not topically consistent',
}

export const SEVERITY_COLOURS: Record<string, string> = {
  high: 'var(--color-severity-high, #ef4444)',
  medium: 'var(--color-severity-medium, #f59e0b)',
  low: 'var(--color-severity-low, #3b82f6)',
}

// Maps the on-disk feedback.yaml `lesson_status` value to a CSS-module
// class. `processing` piggy-backs on the pending treatment; `orphaned`
// on best-effort. Kept in one place so FindingCard, AnalysisHistoryView
// and any future consumer render the same badge for the same status.
export const FEEDBACK_STATUS_CLASS: Record<string, string> = {
  pending: s.pending,
  processing: s.pending,
  validated: s.validated,
  best_effort: s.bestEffort,
  error: s.error,
  orphaned: s.bestEffort,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return YYYY-MM-DD string for a Date, using local time. */
export function toDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Default "from" date: 3 days ago. */
export function defaultDateFrom(): string {
  const d = new Date()
  d.setDate(d.getDate() - 3)
  return toDateStr(d)
}

/** Default "to" date: today. */
export function defaultDateTo(): string {
  return toDateStr(new Date())
}

/**
 * Format a `provider/model` string for a dropdown option. Strips the
 * provider prefix for readability, but keeps the full string when another
 * entry in *allModels* has the same bare name (e.g. `ollama/gemma4:26b`
 * vs `ollama_chat/gemma4:26b`) so collisions remain distinguishable.
 */
export function formatModelOption(model: string, allModels: readonly string[]): string {
  const bare = model.replace(/^[^/]+\//, '')
  const hasCollision = allModels.some(
    (other) => other !== model && other.replace(/^[^/]+\//, '') === bare,
  )
  return hasCollision ? model : bare
}

/** Compact integer token counts: 1234 → "1.2k", 900 → "900". */
export function formatTokenCount(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}
