import React from 'react'
import { FormattedMessageBody } from './FormattedMessageBody'
import { RequestIdBadge } from './RequestIdBadge'
import { useWebSocket } from '../websocketContext'
import { TtsStatusIndicator } from './TtsStatusIndicator'
import { GuidanceButton } from './GuidanceButton'
import { useSpeechRecognition } from '../hooks/useSpeechRecognition'
import { useChatStepLabel } from '../hooks/useChatStepLabel'
import { useChatBannerManager } from '../hooks/useChatBannerManager'
import { useChatTtsAutoPlay } from '../hooks/useChatTtsAutoPlay'
import { useStickyBottomScroll } from '../hooks/useStickyBottomScroll'
import { useHistoryScrollback } from '../hooks/useHistoryScrollback'
import { dayLabel, isNewDay } from '../utils/dayLabel'
import { correlateOutboundRequestIds } from '../utils/correlateOutboundRequestIds'
import { getDirectiveOutboundIds } from '../utils/getDirectiveOutboundIds'
import { getFailedOutboundErrors } from '../utils/getFailedOutboundErrors'
import { outboundClientMsgId } from '../utils/outboundClientMsgId'
import cs from './ChatPanel.module.css'

function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds))
  if (safe < 60) return `${safe}s`
  const mins = Math.floor(safe / 60)
  const secs = safe % 60
  return secs === 0 ? `${mins}m` : `${mins}m ${secs}s`
}

function formatMessageTimestamp(ms: number): string {
  const date = new Date(ms)
  const now = new Date()
  const time = date.toLocaleTimeString()

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const messageDay = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const diffDays = Math.round((today.getTime() - messageDay.getTime()) / 86_400_000)

  if (diffDays === 0) return time
  if (diffDays === 1) return `Yesterday ${time}`
  if (diffDays < 7) return `${date.toLocaleDateString(undefined, { weekday: 'long' })} ${time}`
  return `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${time}`
}

interface ChatPanelProps {
  isAutoTtsEnabled: boolean
  isDevMode: boolean
  isMobile?: boolean
  /** Auto-TTS only fires when `'healthy'`. `'checking'` and `'failing'`
   *  skip silently so an outage doesn't spam the error banner. */
  ttsHealth?: 'checking' | 'healthy' | 'failing'
  /** Empty string disables TTS playback. */
  ttsWsUrl: string
}

const EMPTY_STATE_PROMPTS = [
  'Play the most popular UK song from 1976',
  'Queue a couple of jazz albums on the default zone',
  'Play 20 random tracks from all my Pink Floyd albums',
]

export const ChatPanel: React.FC<ChatPanelProps> = ({
  isAutoTtsEnabled, isDevMode, isMobile,
  ttsHealth = 'healthy', ttsWsUrl,
}) => {
  const {
    status, messages, sendMessage, isLlmActive, trimmedCount,
    requestHistory, reachedBeginning, historyBatchToken,
  } = useWebSocket()
  const [draft, setDraft] = React.useState('')
  const speech = useSpeechRecognition()
  const scrollContainerRef = React.useRef<HTMLDivElement | null>(null)
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null)

  const chatMessages = React.useMemo(
    () => messages.filter((m) => m.channel === 'chat'),
    [messages],
  )

  const outboundRequestIds = React.useMemo(
    () => correlateOutboundRequestIds(messages),
    [messages],
  )

  const directiveOutboundIds = React.useMemo(
    () => getDirectiveOutboundIds(messages),
    [messages],
  )

  const failedOutboundErrors = React.useMemo(
    () => getFailedOutboundErrors(messages),
    [messages],
  )

  const visibleChatMessages = chatMessages
  const isLlmCallInProgress = isLlmActive

  const stepProgress = useChatStepLabel(messages, trimmedCount)
  const { banners, isRateLimited, addTransientErrorBanner } = useChatBannerManager(
    messages, trimmedCount,
  )
  const ttsStatus = useChatTtsAutoPlay(chatMessages, {
    isAutoTtsEnabled, ttsHealth, ttsWsUrl, addTransientErrorBanner,
  })

  useStickyBottomScroll(scrollContainerRef, 'chat')
  useHistoryScrollback(
    scrollContainerRef, messages, requestHistory, reachedBeginning ?? false, historyBatchToken ?? 0,
  )

  // Explicit affordance to pull older history — works even when today's
  // content doesn't overflow (so there's no scrollbar to drag up).
  const loadEarlier = React.useCallback(() => {
    if (messages.length > 0) requestHistory?.(messages[0].timestamp - 1)
  }, [messages, requestHistory])
  const canLoadEarlier = !(reachedBeginning ?? false) && messages.length > 0

  // Populate textarea from speech recognition results. Syncing from
  // an external system (Web Speech API) is exactly the case where the
  // setState-in-effect rule's official carve-out applies.
  const { transcript: speechTranscript, resetTranscript, interimTranscript: speechInterim } = speech
  React.useEffect(() => {
    if (speechTranscript) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(speechTranscript)
      resetTranscript()
    }
  }, [speechTranscript, resetTranscript])

  const toggleSpeech = React.useCallback(() => {
    if (speech.isListening) {
      speech.stop()
    } else {
      speech.start()
    }
  }, [speech])

  // Ctrl+M toggles speech recognition globally
  React.useEffect(() => {
    if (!speech.isSupported || isMobile) return
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'm') {
        e.preventDefault()
        toggleSpeech()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [speech.isSupported, isMobile, toggleSpeech])

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (isRateLimited) return
    const trimmed = draft.trim()
    if (!trimmed) return

    sendMessage('chat', trimmed)
    setDraft('')
  }

  const handleRetryNow = () => {
    sendMessage(
      'session-control-request',
      JSON.stringify({
        request_id: crypto.randomUUID(),
        action: 'retry_now',
      }),
    )
  }
  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSubmit(event)
    }
  }

  const handlePromptChipClick = (prompt: string) => {
    setDraft(prompt)
    textareaRef.current?.focus()
  }

  return (
    <div className={`panel ${cs.panelChat} ${isRateLimited ? cs.panelChatRateLimited : ''}`}>
      <div className="panel-header">
        <span className="panel-heading-group">
          <h2>Chat</h2>
          <GuidanceButton id="chat-basics" />
        </span>
        <span className={`status status-${status}`}>{status.toUpperCase()}</span>
      </div>
      {banners.length > 0 ? (
        <div className={cs.rateLimitBannerStack} role="status" aria-live="polite">
          {banners.map((banner) => (
            <div key={banner.id} className={cs.rateLimitBanner}>
              {banner.kind === 'retry' ? (
                <>
                  <span>
                    <strong>{banner.agentName}</strong> is rate limited. Retrying in {banner.countdown}s ({banner.attempt}/
                    {banner.maxRetries}). {banner.error}
                  </span>
                  <button
                    type="button"
                    className={cs.rateLimitRetryButton}
                    onClick={handleRetryNow}
                    disabled={!banner.canOverride}
                  >
                    Retry now
                  </button>
                </>
              ) : (
                <span>
                  <strong>{banner.agentName}</strong> error: {banner.error}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div ref={scrollContainerRef} className="panel-body scrollable">
        {visibleChatMessages.length === 0 && !isLlmCallInProgress ? (
          <div className={cs.emptyState}>
            <p className={cs.emptyStateHint}>Ask me anything &mdash; try one of these:</p>
            <div className={cs.emptyStateChips}>
              {EMPTY_STATE_PROMPTS.map((prompt) => (
                <button
                  type="button"
                  key={prompt}
                  className={cs.emptyStateChip}
                  onClick={() => handlePromptChipClick(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ul className="message-list">
            {canLoadEarlier ? (
              <li className="message-load-earlier">
                <button type="button" onClick={loadEarlier}>Load earlier messages</button>
              </li>
            ) : null}
            {visibleChatMessages.map((m, idx) => {
              const prev = idx > 0 ? visibleChatMessages[idx - 1] : undefined
              const showDaySeparator = !prev || isNewDay(prev.timestamp, m.timestamp)
              const outboundKey = m.direction === 'outbound' ? outboundClientMsgId(m) : undefined
              const isDirective = outboundKey !== undefined && directiveOutboundIds.has(outboundKey)
              const failureError = outboundKey !== undefined ? failedOutboundErrors.get(outboundKey) : undefined
              const msgRequestId = isDirective
                ? undefined
                : m.direction === 'inbound' && m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
                  ? (m.payload as Record<string, unknown>).request_id ?? (m.meta as Record<string, unknown> | undefined)?.request_id
                  : outboundKey !== undefined
                    ? outboundRequestIds.get(outboundKey)
                    : undefined
              const directiveClass = isDirective ? ' message-directive' : ''
              const failedClass = failureError ? ' message-failed' : ''
              return (
                <React.Fragment key={m.id}>
                {showDaySeparator ? (
                  <li className="message-day-separator" aria-hidden="true">
                    <span>{dayLabel(m.timestamp)}</span>
                  </li>
                ) : null}
                <li
                  className={`message message-${m.direction}${directiveClass}${failedClass}`}
                  data-directive={isDirective ? 'true' : undefined}
                  data-failed={failureError ? 'true' : undefined}
                >
                  <span className="message-meta">
                    <span className="message-meta-sender">
                      {isDirective ? 'Directive' : m.direction === 'outbound' ? 'You' : 'Swarpius'}
                      {isDevMode && typeof msgRequestId === 'string' && msgRequestId ? (
                        <>{' '}<RequestIdBadge requestId={msgRequestId} /></>
                      ) : null}
                      {ttsStatus?.messageId === m.id ? (
                        <>{' '}<TtsStatusIndicator phase={ttsStatus.phase} /></>
                      ) : null}
                    </span>
                    <span className="message-meta-time">{formatMessageTimestamp(m.timestamp)}</span>
                  </span>
                  <FormattedMessageBody body={m.body} channel="chat" payload={m.payload} />
                  {failureError ? (
                    <span className="message-failed-pill" title={failureError}>
                      <span aria-hidden="true">⚠</span> Failed
                    </span>
                  ) : null}
                </li>
                </React.Fragment>
              )
            })}
            {isLlmCallInProgress ? (
              <li className="message message-inbound message-processing" aria-live="polite">
                <span className="message-meta">Swarpius</span>
                <div className="message-processing-text">
                  {(stepProgress.steps.length > 0
                    ? stepProgress.steps
                    : [{ label: 'Thinking...', elapsedSec: 0, isActive: true }]
                  ).map((step, idx) => (
                    <div
                      key={`${idx}-${step.label}`}
                      className={
                        'message-processing-step-row ' +
                        (step.isActive ? 'is-active' : 'is-done')
                      }
                    >
                      {step.isActive ? (
                        <span className="message-typing-dots" aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </span>
                      ) : (
                        <span className="message-processing-step-arrow" aria-hidden="true">→</span>
                      )}
                      <span className="message-processing-step-label">{step.label}</span>
                      {step.elapsedSec >= 1 ? (
                        <span className="message-processing-step-elapsed">{formatElapsed(step.elapsedSec)}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </li>
            ) : null}
          </ul>
        )}
      </div>

      <form className="panel-footer input-row" onSubmit={handleSubmit}>
        <div className={cs.composerWrapper}>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder={isMobile ? "Type a message..." : "Type a message and press Enter (Shift+Enter for newline)"}
            rows={isMobile ? 2 : 5}
            disabled={isRateLimited}
          />
          {speech.isListening && speechInterim && (
            <div className={cs.interimTranscript}>{speechInterim}</div>
          )}
        </div>
        <div className={cs.buttonColumn}>
          {!isMobile && (
            // Firefox lacks the Web Speech API — disabled, not hidden.
            // The title is on the wrapper because a disabled button
            // doesn't fire hover events (as in TtsToggle).
            <span
              className={cs.micButtonWrap}
              title={
                !speech.isSupported
                  ? 'Voice input is not supported in this browser'
                  : speech.isListening
                    ? 'Stop listening (Ctrl+M)'
                    : 'Start speech input (Ctrl+M)'
              }
            >
              <button
                type="button"
                className={`${cs.micButton} ${speech.isListening ? cs.micButtonActive : ''}`}
                onClick={toggleSpeech}
                disabled={isRateLimited || !speech.isSupported}
                aria-label={
                  !speech.isSupported
                    ? 'Voice input is not supported in this browser'
                    : speech.isListening
                      ? 'Stop listening'
                      : 'Start speech input'
                }
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" y1="19" x2="12" y2="23" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              </button>
            </span>
          )}
          <button type="submit" disabled={!draft.trim() || isRateLimited}>
            Send
          </button>
        </div>
      </form>
    </div>
  )
}
