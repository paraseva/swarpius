import { useEffect, useState } from 'react'
import { TTS_ERROR_EVENT_NAME, TTS_RECOVERED_EVENT_NAME } from '../tts'

/** Client-side TTS-failing latch driven by `playServerTts` lifecycle
 *  events: true on error, false on recovery. Bridges the gap between
 *  a click-observed failure and the agent's next probe — the agent
 *  push remains authoritative. */
export function useClientTtsHealthOverride(): boolean {
  const [clientTtsFailing, setClientTtsFailing] = useState(false)
  useEffect(() => {
    const onError = () => setClientTtsFailing(true)
    const onRecovered = () => setClientTtsFailing(false)
    window.addEventListener(TTS_ERROR_EVENT_NAME, onError)
    window.addEventListener(TTS_RECOVERED_EVENT_NAME, onRecovered)
    return () => {
      window.removeEventListener(TTS_ERROR_EVENT_NAME, onError)
      window.removeEventListener(TTS_RECOVERED_EVENT_NAME, onRecovered)
    }
  }, [])
  return clientTtsFailing
}
