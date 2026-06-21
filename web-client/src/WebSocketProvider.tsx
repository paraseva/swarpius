import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { APP_WS_URL } from './config'
import { createUuid } from './utils/uuid'
import { insertMessage } from './utils/insertMessage'
import {
  type ChannelId,
  type ConnectionStatus,
  type SocketMessage,
  WebSocketContext,
} from './websocketContext'

interface WebSocketProviderProps {
  children: React.ReactNode
}

const MAX_MESSAGES = 2000
const CHAT_PRESERVE_COUNT = 200

/** Close code sent by the server when another browser/tab has taken
 *  over this session (different session_id on the new connection).
 *  Must match agent/app/constants.py:CLOSE_CODE_SESSION_TAKEOVER. */
const CLOSE_CODE_SESSION_TAKEOVER = 4001

const SESSION_ID_STORAGE_KEY = 'swarpius_session_id'

/** Persisted per-browser session identifier. The server uses this to
 *  enforce single-session hygiene: same ID on a new socket is a silent
 *  reconnect; a different ID takes over and closes the old socket with
 *  CLOSE_CODE_SESSION_TAKEOVER. */
const getOrCreateSessionId = (): string => {
  try {
    const existing = globalThis.localStorage?.getItem(SESSION_ID_STORAGE_KEY)
    if (existing) return existing
    const fresh = createUuid()
    globalThis.localStorage?.setItem(SESSION_ID_STORAGE_KEY, fresh)
    return fresh
  } catch {
    // localStorage can throw in private mode / SSR. A per-tab UUID is a
    // safe fallback — it still registers a unique session server-side.
    return createUuid()
  }
}

/** Channels that the backend sends but no client component consumes. */
const IGNORED_CHANNELS = new Set<string>([])

interface LlmCallEvent {
  event_type?: string
  call_id?: string
}

const trimMessages = (messages: SocketMessage[]): SocketMessage[] => {
  if (messages.length <= MAX_MESSAGES) return messages

  const overflow = messages.length - MAX_MESSAGES
  const candidate = messages.slice(overflow)

  // Ensure at least CHAT_PRESERVE_COUNT recent chat messages survive.
  const chatInCandidate = candidate.filter((m) => m.channel === 'chat').length
  if (chatInCandidate >= CHAT_PRESERVE_COUNT) return candidate

  // Walk backward from overflow point to rescue additional chat messages.
  const needed = CHAT_PRESERVE_COUNT - chatInCandidate
  const rescued: SocketMessage[] = []
  for (let i = overflow - 1; i >= 0 && rescued.length < needed; i -= 1) {
    if (messages[i].channel === 'chat') {
      rescued.push(messages[i])
    }
  }
  return [...rescued.reverse(), ...candidate]
}

export const WebSocketProvider: React.FC<WebSocketProviderProps> = ({ children }) => {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [messageState, setMessageState] = useState<{ messages: SocketMessage[]; trimmedCount: number }>({
    messages: [],
    trimmedCount: 0,
  })
  const messages = messageState.messages
  const trimmedCount = messageState.trimmedCount
  const [isLlmActive, setIsLlmActive] = useState(false)
  const [reachedBeginning, setReachedBeginning] = useState(false)
  const [historyBatchToken, setHistoryBatchToken] = useState(0)
  const [latestZoneSnapshot, setLatestZoneSnapshot] = useState<unknown>(null)
  const [connectionGeneration, setConnectionGeneration] = useState(0)
  const [isRestarting, setIsRestarting] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const shouldReconnectRef = useRef(true)
  const activeCallIdsRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    shouldReconnectRef.current = true

    const clearReconnectTimer = () => {
      if (!reconnectTimerRef.current) return
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    const scheduleReconnect = () => {
      if (!shouldReconnectRef.current) return
      if (reconnectTimerRef.current) return
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null
        if (!shouldReconnectRef.current) return
        setStatus('connecting')
        connect()
      }, 2000)
    }

    const connect = () => {
      const sessionId = getOrCreateSessionId()
      const separator = APP_WS_URL.includes('?') ? '&' : '?'
      const url = `${APP_WS_URL}${separator}session_id=${encodeURIComponent(sessionId)}`
      const socket = new WebSocket(url)
      socketRef.current = socket

      const handleOpen = () => {
        clearReconnectTimer()
        // Wipe accumulated state before the server's replay so the
        // browser ends up as a faithful viewer of whatever the server
        // currently considers history. Bumping the generation lets
        // consumers remount their subtree to drop any local state
        // tied to the previous connection (Restart directives,
        // half-finished dirty-edits, etc.) — the server replays
        // whatever remains relevant.
        setMessageState({ messages: [], trimmedCount: 0 })
        setReachedBeginning(false)
        activeCallIdsRef.current.clear()
        setIsLlmActive(false)
        setConnectionGeneration((g) => g + 1)
        setStatus('open')
        // Reconnecting after a restart-imminent click clears the
        // flag — the new server is alive, so the modal can hide.
        setIsRestarting(false)
      }
      const handleClose = (event: CloseEvent) => {
        // Ignore close events for sockets already superseded by a newer
        // connect() — otherwise we schedule a reconnect for a socket
        // nobody's using and trigger a cascade against the live one.
        if (socket !== socketRef.current) return
        if (event.code === CLOSE_CODE_SESSION_TAKEOVER) {
          shouldReconnectRef.current = false
          clearReconnectTimer()
          setStatus('taken_over')
          return
        }
        setStatus('closed')
        scheduleReconnect()
      }
      const handleError = () => {
        if (socket !== socketRef.current) return
        setStatus('error')
        scheduleReconnect()
      }
      const handleMessage = (event: MessageEvent) => {
        let channel: ChannelId = 'chat'
        const rawBody = String(event.data)
        let payload: unknown
        let meta: Record<string, unknown> | undefined

        try {
          const parsed = JSON.parse(rawBody) as {
            channel?: string
            payload?: unknown
            body?: string
            message?: string
            meta?: Record<string, unknown>
          }

          if (parsed.channel) {
            channel = parsed.channel as ChannelId
          }

          payload = parsed.payload
          if (parsed.meta && typeof parsed.meta === 'object' && !Array.isArray(parsed.meta)) {
            meta = parsed.meta
          }
        } catch {
          // Keep raw fallback body for non-JSON websocket messages.
        }

        // Zone snapshots fire ~1 Hz during playback and are latest-only.
        // Routing them to a dedicated slot keeps the bounded `messages`
        // array from being dominated by stale snapshots whose size also
        // bloats DevTools-instrumented render details.
        if (channel === 'zone-snapshots') {
          setLatestZoneSnapshot(payload ?? null)
          return
        }

        // Passive history-cursor signal: not a message, just whether older
        // history exists past what's now loaded. Drives "can I scroll back".
        if (channel === 'history-cursor') {
          const hasOlder = (payload as { has_older?: boolean } | undefined)?.has_older
          setReachedBeginning(hasOlder === false)
          // The cursor closes a history batch (it's sent after the day's
          // messages). Bumping the token lets scroll-back release its in-flight
          // guard exactly when the batch is fully delivered.
          setHistoryBatchToken((t) => t + 1)
          return
        }

        if (IGNORED_CHANNELS.has(channel)) return

        // Incremental active-call tracking.
        if (channel === 'llm-diagnostics' && payload && typeof payload === 'object' && !Array.isArray(payload)) {
          const llmEvent = payload as LlmCallEvent
          if (llmEvent.call_id) {
            if (llmEvent.event_type === 'call_started') {
              activeCallIdsRef.current.add(llmEvent.call_id)
            } else if (llmEvent.event_type === 'call_completed' || llmEvent.event_type === 'call_failed') {
              activeCallIdsRef.current.delete(llmEvent.call_id)
            }
            setIsLlmActive(activeCallIdsRef.current.size > 0)
          }
        }

        // Replayed user messages arrive from the server but should render
        // as outbound (user bubbles). Extract the plain body text so
        // ChatPanel renders them the same as live outbound messages.
        const isReplayedOutbound = meta?.replay === true && meta?.direction === 'outbound'
        const direction: 'inbound' | 'outbound' = isReplayedOutbound ? 'outbound' : 'inbound'
        const messageBody = isReplayedOutbound
          ? String((payload as Record<string, unknown>)?.body ?? '')
          : rawBody

        const record: SocketMessage = {
          id: createUuid(),
          channel,
          direction,
          body: messageBody,
          payload,
          meta,
          timestamp: typeof meta?.created_at === 'number' ? meta.created_at : Date.now(),
        }
        setMessageState((prev) => {
          const next = insertMessage(prev.messages, record)
          if (next === prev.messages) return prev  // duplicate (server id seen)
          // Trim only when a live message lands at the end (the bounded live
          // tail); never trim a historical prepend, or scroll-back would undo
          // itself.
          const appendedAtEnd = next[next.length - 1] === record
          if (appendedAtEnd && next.length > MAX_MESSAGES) {
            const beforeLen = next.length
            const trimmed = trimMessages(next)
            return { messages: trimmed, trimmedCount: prev.trimmedCount + (beforeLen - trimmed.length) }
          }
          return { messages: next, trimmedCount: prev.trimmedCount }
        })
      }

      socket.addEventListener('open', handleOpen)
      socket.addEventListener('close', handleClose)
      socket.addEventListener('error', handleError)
      socket.addEventListener('message', handleMessage)
    }

    connect()

    return () => {
      shouldReconnectRef.current = false
      clearReconnectTimer()
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [])

  const sendMessage = useCallback((channel: ChannelId, body: string) => {
    const socket = socketRef.current

    const record: SocketMessage = {
      id: createUuid(),
      channel,
      direction: 'outbound',
      body,
      timestamp: Date.now(),
    }

    setMessageState((prev) => {
      const next = [...prev.messages, record]
      if (next.length <= MAX_MESSAGES) return { messages: next, trimmedCount: prev.trimmedCount }
      const beforeLen = next.length
      const trimmed = trimMessages(next)
      return { messages: trimmed, trimmedCount: prev.trimmedCount + (beforeLen - trimmed.length) }
    })

    if (socket && socket.readyState === WebSocket.OPEN) {
      const payload = JSON.stringify({ channel, body, client_msg_id: record.id })
      socket.send(payload)
    }

    return record.id
  }, [])

  const markRestarting = useCallback(() => setIsRestarting(true), [])

  const clearMessages = useCallback(() => {
    setMessageState({ messages: [], trimmedCount: 0 })
  }, [])

  // Fire-and-forget: ask the server for the most recent non-empty day at or
  // before beforeMs. The reply arrives as ordinary messages on their channels
  // (handled by the passive receive above) plus a history-cursor signal — no
  // response correlation here.
  const requestHistory = useCallback((beforeMs: number) => {
    const socket = socketRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({
        channel: 'history-request',
        body: JSON.stringify({ before_ms: beforeMs }),
      }))
    }
  }, [])

  const value = useMemo(
    () => ({
      status,
      messages,
      sendMessage,
      clearMessages,
      requestHistory,
      reachedBeginning,
      historyBatchToken,
      isLlmActive,
      latestZoneSnapshot,
      trimmedCount,
      connectionGeneration,
      isRestarting,
      markRestarting,
    }),
    [
      status, messages, sendMessage, clearMessages, requestHistory, reachedBeginning,
      historyBatchToken, isLlmActive, latestZoneSnapshot,
      trimmedCount, connectionGeneration, isRestarting, markRestarting,
    ],
  )

  return <WebSocketContext.Provider value={value}>{children}</WebSocketContext.Provider>
}
