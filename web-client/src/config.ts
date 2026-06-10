const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

const getCurrentBrowserHost = (): string | null => {
  if (typeof window === 'undefined') {
    return null
  }

  return window.location.hostname || null
}

const replaceLoopbackHostInWsUrl = (rawUrl: string): string => {
  const currentHost = getCurrentBrowserHost()
  if (!currentHost) {
    return rawUrl
  }

  try {
    const parsed = new URL(rawUrl)
    if (!LOOPBACK_HOSTS.has(parsed.hostname.toLowerCase())) {
      return rawUrl
    }

    parsed.hostname = currentHost
    return parsed.toString()
  } catch {
    return rawUrl
  }
}

const defaultAppWebSocketUrl = (): string => {
  const host = getCurrentBrowserHost() || 'localhost'
  const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${host}:8080/ws`
}

const resolveAppWebSocketUrl = (): string => {
  const explicitUrl = import.meta.env.VITE_WS_URL?.trim()
  if (!explicitUrl) {
    return defaultAppWebSocketUrl()
  }

  return replaceLoopbackHostInWsUrl(explicitUrl)
}

export const APP_WS_URL = resolveAppWebSocketUrl()

/**
 * Derive the TTS proxy URL from the chat WS URL. The agent serves
 * the TTS proxy on the same host + port at ``/tts`` instead of
 * ``/ws``, so the two URLs always vary by path only — one firewall
 * rule, one TLS cert, no separate config.
 *
 * Returns ``''`` for empty / malformed inputs so callers can
 * unambiguously treat "no URL" as "TTS unavailable".
 */
export const deriveTtsWebSocketUrl = (chatWsUrl: string): string => {
  if (!chatWsUrl) return ''
  try {
    const parsed = new URL(chatWsUrl)
    parsed.pathname = '/tts'
    parsed.search = ''
    return parsed.toString()
  } catch {
    return ''
  }
}
