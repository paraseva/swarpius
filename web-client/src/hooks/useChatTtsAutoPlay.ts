import React from 'react'
import { parseMessageBody } from '../utils/formatMessageBody'
import { sanitiseTtsText } from '../utils/sanitiseTtsText'
import { playServerTts, TTS_ERROR_EVENT_NAME, type TtsStatusPhase } from '../tts'
import type { TtsIndicatorPhase } from '../components/TtsStatusIndicator'
import type { SocketMessage } from '../websocketContext'

const MAX_FULL_SPEAK_CHARS = 320
const MAX_FULL_SPEAK_LIST_LINES = 3

const isLikelyLongListMessage = (text: string): boolean => {
  const lines = text.split('\n').map((line) => line.trim())
  const listLineCount = lines.filter((line) => /^(\d+[).\s]|[-*]\s)/.test(line)).length
  return listLineCount > MAX_FULL_SPEAK_LIST_LINES
}

const getSpeakTextForMessage = (
  body: string,
  meta?: Record<string, unknown>,
  payload?: unknown,
): string | null => {
  const metaSpeakText = typeof meta?.speak_text === 'string' ? meta.speak_text.trim() : null
  if (metaSpeakText !== null) {
    return sanitiseTtsText(metaSpeakText) || null
  }

  let displayText = body.trim()
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    const chatResponse = (payload as Record<string, unknown>).chat_response
    if (typeof chatResponse === 'string' && chatResponse.trim()) {
      displayText = chatResponse.trim()
    }
  }

  displayText = sanitiseTtsText(displayText)
  if (!displayText) return null
  if (displayText.length <= MAX_FULL_SPEAK_CHARS && !isLikelyLongListMessage(displayText)) {
    return displayText
  }

  const firstSentence = displayText.match(/.+?[.!?](\s|$)/)?.[0]?.trim() ?? ''
  if (firstSentence && firstSentence.length <= MAX_FULL_SPEAK_CHARS) {
    return firstSentence
  }
  return null
}

export interface UseChatTtsAutoPlayOptions {
  isAutoTtsEnabled: boolean
  ttsHealth: 'checking' | 'healthy' | 'failing'
  ttsWsUrl: string
  addTransientErrorBanner: (
    agentName: string,
    error: string,
    displaySeconds?: number,
    id?: string,
  ) => void
}

export interface ChatTtsStatus {
  messageId: string
  phase: TtsIndicatorPhase
}

/**
 * Plays auto-TTS for the latest inbound chat message when enabled and
 * TTS health is good. Also listens for global TTS error events and
 * surfaces them through the banner manager. Returns the current
 * indicator state (``null`` when no TTS activity is in flight).
 */
export function useChatTtsAutoPlay(
  chatMessages: SocketMessage[],
  {
    isAutoTtsEnabled,
    ttsHealth,
    ttsWsUrl,
    addTransientErrorBanner,
  }: UseChatTtsAutoPlayOptions,
): ChatTtsStatus | null {
  const [ttsStatus, setTtsStatus] = React.useState<ChatTtsStatus | null>(null)
  const lastSpokenId = React.useRef<string | null>(null)

  React.useEffect(() => {
    const onTtsError = (event: Event) => {
      const customEvent = event as CustomEvent<{ message?: string }>
      const detailMessage = (customEvent.detail?.message || '').trim()
      addTransientErrorBanner('TTS', detailMessage || 'Unable to reach TTS server.', 5)
    }
    window.addEventListener(TTS_ERROR_EVENT_NAME, onTtsError as EventListener)
    return () => window.removeEventListener(TTS_ERROR_EVENT_NAME, onTtsError as EventListener)
  }, [addTransientErrorBanner])

  const latestChatMessageId = chatMessages[chatMessages.length - 1]?.id ?? null

  React.useEffect(() => {
    if (chatMessages.length === 0) return

    const latest = chatMessages[chatMessages.length - 1]
    if (latest.direction !== 'inbound') return
    if (latest.id === lastSpokenId.current) return
    if (!isAutoTtsEnabled || latest.meta?.replay) {
      lastSpokenId.current = latest.id
      return
    }
    if (ttsHealth !== 'healthy') {
      lastSpokenId.current = latest.id
      return
    }

    const parsed = parseMessageBody(latest.body, 'chat', latest.payload)
    if (parsed.source?.startsWith('[Details from')) {
      lastSpokenId.current = latest.id
      return
    }

    const speakText = getSpeakTextForMessage(latest.body, latest.meta, latest.payload)
    lastSpokenId.current = latest.id
    if (!speakText) return
    const messageId = latest.id
    const handleStatus = (phase: TtsStatusPhase) => {
      if (phase === 'sending' || phase === 'playing') {
        setTtsStatus({ messageId, phase })
      } else {
        setTtsStatus((prev) => (prev?.messageId === messageId ? null : prev))
      }
    }
    if (!ttsWsUrl) return
    playServerTts(speakText, ttsWsUrl, handleStatus).catch((error) => {
      console.error('playServerTts error:', error)
    })
  }, [chatMessages, latestChatMessageId, isAutoTtsEnabled, ttsHealth, ttsWsUrl])

  return ttsStatus
}
