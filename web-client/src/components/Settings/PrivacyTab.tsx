import React from 'react'
import f from './fields.module.css'
import p from './PrivacyTab.module.css'
import {
  useWebSocket,
  type ChannelId,
  type SocketMessage,
} from '../../websocketContext'

type Phase = 'idle' | 'confirming' | 'clearing' | 'done' | 'error'

interface ClearResponse {
  request_id?: string
  ok?: boolean
  reason?: string
}

function parseResponse(message: SocketMessage): ClearResponse | null {
  const raw = message.payload ?? message.body
  if (raw && typeof raw === 'object') return raw as ClearResponse
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as ClearResponse
    } catch {
      return null
    }
  }
  return null
}

interface ClearActionProps {
  label: string
  warning: string
  doneMessage: string
  requestChannel: ChannelId
  responseChannel: ChannelId
  disabled?: boolean
  disabledHint?: string
  onSuccess?: () => void
}

const ClearAction: React.FC<ClearActionProps> = ({
  label, warning, doneMessage, requestChannel, responseChannel,
  disabled, disabledHint, onSuccess,
}) => {
  const { sendMessage, messages } = useWebSocket()
  const [phase, setPhase] = React.useState<Phase>('idle')
  const [error, setError] = React.useState('')
  const pendingId = React.useRef<string | null>(null)

  React.useEffect(() => {
    if (phase !== 'clearing' || !pendingId.current) return
    const match = messages.find(
      (m) =>
        m.channel === responseChannel &&
        m.direction === 'inbound' &&
        parseResponse(m)?.request_id === pendingId.current,
    )
    if (!match) return
    const payload = parseResponse(match)
    pendingId.current = null
    if (payload?.ok) {
      onSuccess?.()
      setPhase('done')
    } else {
      setError(payload?.reason || 'Could not complete the request. Please try again.')
      setPhase('error')
    }
  }, [messages, phase, responseChannel, onSuccess])

  const confirm = () => {
    const requestId = crypto.randomUUID()
    pendingId.current = requestId
    setError('')
    setPhase('clearing')
    sendMessage(requestChannel, JSON.stringify({ request_id: requestId }))
  }

  const showButton = phase === 'idle' || phase === 'done' || phase === 'error'

  return (
    <div className={p.action}>
      {showButton ? (
        <button
          type="button"
          className={`${p.button} ${p.trigger}`}
          onClick={() => setPhase('confirming')}
          disabled={disabled}
        >
          {label}
        </button>
      ) : null}
      {disabled && disabledHint ? <p className={p.hint}>{disabledHint}</p> : null}
      {phase === 'confirming' ? (
        <div className={p.confirm} role="group" aria-label={label}>
          <p className={p.warning}>{warning}</p>
          <div className={p.confirmButtons}>
            <button type="button" className={`${p.button} ${p.danger}`} onClick={confirm}>
              Yes, clear it
            </button>
            <button type="button" className={p.button} onClick={() => setPhase('idle')}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {phase === 'clearing' ? <p className={p.hint}>Clearing…</p> : null}
      {phase === 'done' ? <p className={p.status} role="status">{doneMessage}</p> : null}
      {phase === 'error' ? <p className={p.error} role="alert">{error}</p> : null}
    </div>
  )
}

/**
 * Privacy & Data tab. An action tab (not a settings form): it does not
 * participate in Save & Validate. Lets the user delete the locally-stored
 * conversation history + the assistant's working memory, and the listening
 * history, independently.
 */
export const PrivacyTab: React.FC = () => {
  const { clearMessages, isLlmActive } = useWebSocket()

  return (
    <div>
      <p className={f.tabIntro}>
        Swarpius stores your chat history, the assistant&apos;s working memory,
        and a record of what you&apos;ve played — on this machine — so a
        restart resumes where you left off and you can ask about past
        listening. Your saved zones and other settings are kept.
      </p>

      <div className={p.actions}>
      <ClearAction
        label="Clear conversation history"
        warning="This permanently deletes your chat history and the assistant's memory of this conversation. It cannot be undone."
        doneMessage="Conversation history cleared."
        requestChannel="clear-conversation-request"
        responseChannel="clear-conversation-response"
        disabled={isLlmActive}
        disabledHint="Finish the current request before clearing conversation history."
        onSuccess={clearMessages}
      />

      <ClearAction
        label="Clear listening history"
        warning="This permanently deletes the record of what you've played. It cannot be undone."
        doneMessage="Listening history cleared."
        requestChannel="clear-listening-history-request"
        responseChannel="clear-listening-history-response"
      />
      </div>
    </div>
  )
}
