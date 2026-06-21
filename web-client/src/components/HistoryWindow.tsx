import React from 'react'
import { FormattedMessageBody } from './FormattedMessageBody'
import { JsonTreeView } from './JsonTreeView'
import { RequestIdBadge } from './RequestIdBadge'
import { useStickyBottomScroll } from '../hooks/useStickyBottomScroll'
import { useRequestFocusSync } from '../hooks/useRequestFocusSync'
import { type ChannelId, useWebSocket } from '../websocketContext'

interface HistoryWindowProps {
  title: string
  channel: ChannelId
  /** When set, this panel participates in the request sync (clicking a badge
   *  elsewhere scrolls it to that request, and its own badges focus). */
  syncKey?: string
}

export const HistoryWindow: React.FC<HistoryWindowProps> = ({
  title,
  channel,
  syncKey,
}) => {
  const { messages } = useWebSocket()
  const scrollContainerRef = React.useRef<HTMLDivElement | null>(null)
  const storageKey = `swarpius:history-window:raw:${channel}`
  const [showRawPayload, setShowRawPayload] = React.useState(() => {
    try {
      return window.localStorage.getItem(storageKey) === '1'
    } catch {
      return false
    }
  })

  const channelMessages = React.useMemo(
    () => messages.filter((m) => m.channel === channel),
    [messages, channel],
  )

  useStickyBottomScroll(scrollContainerRef, `history:${channel}`)
  useRequestFocusSync(scrollContainerRef, syncKey)

  React.useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, showRawPayload ? '1' : '0')
    } catch {
      // Ignore storage failures.
    }
  }, [showRawPayload, storageKey])

  const getRawPayload = (message: (typeof channelMessages)[number]): { data: unknown } | { text: string } => {
    if (message.payload !== undefined) return { data: message.payload }
    return { text: message.body }
  }

  const getErrorSeverity = (message: (typeof channelMessages)[number]): 'critical' | 'warning' | 'info' | null => {
    if (channel !== 'errors') return null
    const text = message.body.toLowerCase()
    const payloadStr = message.payload ? JSON.stringify(message.payload).toLowerCase() : ''
    const combined = text + payloadStr
    if (
      combined.includes('traceback') ||
      combined.includes('uncaught') ||
      combined.includes('fatal') ||
      combined.includes('crash') ||
      combined.includes('unhandled')
    ) {
      return 'critical'
    }
    if (
      combined.includes('timeout') ||
      combined.includes('rate_limit') ||
      combined.includes('rate-limit') ||
      combined.includes('retry') ||
      combined.includes('failed')
    ) {
      return 'warning'
    }
    return 'info'
  }

  const getToolLabel = (message: (typeof channelMessages)[number]): string | null => {
    if (message.payload && typeof message.payload === 'object' && !Array.isArray(message.payload)) {
      const structured = message.payload as Record<string, unknown>
      if (typeof structured.label === 'string') {
        return structured.label
      }
      if (typeof structured.source === 'string') {
        const source = structured.source.trim()
        const match = source.match(/:\s*([^\]]+)\]$/)
        if (match?.[1]) return match[1]
      }
    }

    const firstLine = message.body.split('\n', 1)[0]?.trim()
    if (!firstLine) return null
    if (firstLine.startsWith('[') && firstLine.endsWith(']')) {
      const raw = firstLine.slice(1, -1)
      const colonIdx = raw.lastIndexOf(':')
      if (colonIdx !== -1) {
        return raw.slice(colonIdx + 1).trim()
      }
      const suffixMatch = raw.match(/(.*\btool\s+(?:input|output))$/i)
      if (suffixMatch?.[1]) return suffixMatch[1].trim()
    }
    return null
  }

  return (
    <div className="panel panel-history">
      <div className="panel-header panel-header-with-centred-actions">
        <h3>{title}</h3>
        <div className="panel-header-actions">
          <button
            type="button"
            className="panel-view-toggle"
            onClick={() => setShowRawPayload((current) => !current)}
            aria-pressed={showRawPayload}
            title={showRawPayload ? `Show formatted ${title}` : `Show raw ${title} payload`}
            aria-label={showRawPayload ? `Show formatted ${title} messages` : `Show raw ${title} payload`}
          >
            {showRawPayload ? 'Formatted' : 'Raw'}
          </button>
        </div>
      </div>

      <div ref={scrollContainerRef} className="panel-body scrollable">
        {channelMessages.length === 0 ? (
          <p className="empty-placeholder">No events yet.</p>
        ) : (
          <ul className="message-list">
            {channelMessages.map((m, index) => {
              const toolLabel = channel === 'tool-outputs' ? getToolLabel(m) : null
              const errorSeverity = getErrorSeverity(m)
              const isToolPairStart = Boolean(index > 0 && toolLabel && /tool input$/i.test(toolLabel))
              const msgRequestId =
                m.direction === 'inbound' && m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
                  ? (m.payload as Record<string, unknown>).request_id
                  : undefined
              return (
              <li
                key={m.id}
                data-request-id={typeof msgRequestId === 'string' ? msgRequestId : undefined}
                className={`message message-${m.direction} ${isToolPairStart ? 'message-tool-pair-start' : ''} ${errorSeverity ? `message-error-severity-${errorSeverity}` : ''}`}
              >
                <span className="message-meta">
                  <span>
                    {new Date(m.timestamp).toLocaleTimeString()} ·{' '}
                    {m.direction === 'outbound' ? 'Client' : 'Swarpius'}
                    <span className="message-meta-date">{new Date(m.timestamp).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</span>
                  </span>
                  {typeof msgRequestId === 'string' && msgRequestId ? (
                    <RequestIdBadge requestId={msgRequestId} syncKey={syncKey} />
                  ) : null}
                </span>
                {showRawPayload ? (
                  (() => {
                    const raw = getRawPayload(m)
                    return 'data' in raw
                      ? <JsonTreeView data={raw.data} className="message-tree" />
                      : <pre className="message-pre">{raw.text}</pre>
                  })()
                ) : (
                  <FormattedMessageBody body={m.body} channel={channel} payload={m.payload} />
                )}
              </li>
              )
            })}
          </ul>
        )}
      </div>

    </div>
  )
}
