import React from 'react'
import { parseJson } from '../utils/parseJson'
import type { SocketMessage } from '../websocketContext'

interface CoordinatorStepPayload {
  event_type?: string
  selected_skill?: string | null
  display_label?: string | null
  done?: boolean
  step?: number
  tool_call_id?: string | null
  tool_name?: string | null
  timestamp_ms?: number
}

const PLACEHOLDER_LABELS = new Set(['Thinking...', 'Classifying...'])

export interface ChatStepProgressEntry {
  label: string
  elapsedSec: number
  isActive: boolean
}

export interface ChatStepProgress {
  /** Per-step entries in chronological order. Multiple entries may be
   *  active concurrently when the coordinator dispatches parallel
   *  tools — each tool gets its own row and timer. */
  steps: ChatStepProgressEntry[]
}

interface InternalEntry {
  label: string
  startedAt: number
  endedAt: number | null
  /** ID from `tool_call_started`; null for placeholder entries
   *  ("Thinking...", "Classifying..."). */
  toolCallId: string | null
}

const EMPTY_INTERNAL: InternalEntry[] = []

function isPlaceholder(entry: InternalEntry): boolean {
  return PLACEHOLDER_LABELS.has(entry.label)
}

function hasNonPlaceholderActive(entries: InternalEntry[]): boolean {
  return entries.some((e) => e.endedAt === null && !isPlaceholder(e))
}

function trailingActivePlaceholder(entries: InternalEntry[]): InternalEntry | null {
  const last = entries[entries.length - 1]
  if (last && last.endedAt === null && isPlaceholder(last)) return last
  return null
}

function transitionPlaceholder(
  entries: InternalEntry[],
  label: string,
  now: number,
): InternalEntry[] {
  // Used for diagnostic_active / request_id_assignment / post-tool
  // Thinking. If a trailing placeholder is already active, replace it
  // (placeholders don't stack); otherwise append a new active one.
  if (trailingActivePlaceholder(entries)) {
    return [
      ...entries.slice(0, -1),
      { label, startedAt: now, endedAt: null, toolCallId: null },
    ]
  }
  return [
    ...entries,
    { label, startedAt: now, endedAt: null, toolCallId: null },
  ]
}

function appendToolStarted(
  entries: InternalEntry[],
  label: string,
  toolCallId: string | null,
  now: number,
): InternalEntry[] {
  // A real tool entry replaces a trailing placeholder; parallel tool
  // entries don't freeze prior actives (they coexist).
  if (trailingActivePlaceholder(entries)) {
    return [
      ...entries.slice(0, -1),
      { label, startedAt: now, endedAt: null, toolCallId },
    ]
  }
  return [
    ...entries,
    { label, startedAt: now, endedAt: null, toolCallId },
  ]
}

function freezeByToolCallId(
  entries: InternalEntry[],
  toolCallId: string | null,
  now: number,
): InternalEntry[] {
  if (!toolCallId) return entries
  return entries.map((e) => {
    if (e.endedAt === null && e.toolCallId === toolCallId) {
      return { ...e, endedAt: now }
    }
    return e
  })
}

function appendThinkingIfNoToolsActive(
  entries: InternalEntry[],
  now: number,
): InternalEntry[] {
  if (hasNonPlaceholderActive(entries)) return entries
  if (trailingActivePlaceholder(entries)) return entries
  return [
    ...entries,
    { label: 'Thinking...', startedAt: now, endedAt: null, toolCallId: null },
  ]
}

function endAllActive(entries: InternalEntry[], now: number): InternalEntry[] {
  // Drop trailing placeholders; freeze any remaining active entries.
  // Placeholders are stripped wherever they sit so the trail doesn't
  // freeze a "Thinking..." in the middle of completed work.
  return entries
    .filter((e) => !(e.endedAt === null && isPlaceholder(e)))
    .map((e) => (e.endedAt === null ? { ...e, endedAt: now } : e))
}

/**
 * Drive the chat-panel processing indicator from agent-outputs events.
 *
 * Returns a chronological list of per-step entries. Multiple entries
 * may be active (`isActive === true`) at the same time when parallel
 * tools are dispatched within a single coordinator step; each has its
 * own elapsed-time counter anchored on its own `tool_call_started`.
 *
 * Event taxonomy: `tool_call_started` adds an active entry,
 * `tool_call_completed` freezes the matching entry by `tool_call_id`
 * and inserts a "Thinking..." placeholder once no real tools remain
 * active. `coordinator_step` with `done: true` is the terminator;
 * `request_complete` clears the trail.
 */
export function useChatStepLabel(
  messages: SocketMessage[],
  trimmedCount: number,
): ChatStepProgress {
  const [entries, setEntries] = React.useState<InternalEntry[]>(EMPTY_INTERNAL)
  const stepProcessedRef = React.useRef(0)
  const [now, setNow] = React.useState<number>(() => Date.now())

  React.useEffect(() => {
    const startIdx = Math.max(0, stepProcessedRef.current - trimmedCount)

    setEntries((prev) => {
      let next = prev
      let mutated = false

      for (let i = startIdx; i < messages.length; i++) {
        const msg = messages[i]
        if (msg.channel !== 'agent-outputs' || msg.direction !== 'inbound') continue
        const payload = parseJson<CoordinatorStepPayload>(msg.payload ?? msg.body)
        if (!payload) continue

        // Prefer server timestamp_ms so replayed events preserve true elapsed time.
        const eventNow = payload.timestamp_ms ?? Date.now()

        if (payload.event_type === 'request_complete') {
          next = EMPTY_INTERNAL
          mutated = true
        } else if (payload.event_type === 'diagnostic_active') {
          next = transitionPlaceholder(next, 'Classifying...', eventNow)
          mutated = true
        } else if (payload.event_type === 'request_id_assignment') {
          next = transitionPlaceholder(next, 'Thinking...', eventNow)
          mutated = true
        } else if (payload.event_type === 'tool_call_started') {
          const label = payload.display_label ?? `Running ${payload.tool_name ?? 'tool'}`
          next = appendToolStarted(next, label, payload.tool_call_id ?? null, eventNow)
          mutated = true
        } else if (payload.event_type === 'tool_call_completed') {
          next = freezeByToolCallId(next, payload.tool_call_id ?? null, eventNow)
          next = appendThinkingIfNoToolsActive(next, eventNow)
          mutated = true
        } else if (payload.event_type === 'coordinator_step' && payload.done) {
          next = endAllActive(next, eventNow)
          mutated = true
        }
      }

      stepProcessedRef.current = messages.length + trimmedCount
      return mutated ? next : prev
    })
  }, [messages, trimmedCount])

  // Tick `now` every second while at least one entry is active.
  // Anchor the interval on the latest active startedAt — when a new
  // entry begins, the interval re-aligns so its badge ticks at exactly
  // 1s past its own startedAt rather than catching the previous
  // interval mid-cycle.
  const latestActiveStartedAt = React.useMemo(() => {
    let latest: number | null = null
    for (const e of entries) {
      if (e.endedAt === null) {
        if (latest === null || e.startedAt > latest) latest = e.startedAt
      }
    }
    return latest
  }, [entries])

  React.useEffect(() => {
    if (latestActiveStartedAt === null) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [latestActiveStartedAt])

  const steps: ChatStepProgressEntry[] = React.useMemo(() => {
    return entries.map((e) => {
      const end = e.endedAt ?? now
      return {
        label: e.label,
        elapsedSec: Math.max(0, Math.floor((end - e.startedAt) / 1000)),
        isActive: e.endedAt === null,
      }
    })
  }, [entries, now])

  return { steps }
}
