export const parseJson = <T,>(raw: unknown): T | null => {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as T
  }
  if (typeof raw !== 'string') {
    return null
  }
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

type InboundPayloadOptions = {
  requireRequestId?: boolean
  requireType?: boolean
}

/**
 * Parse + shape-check an inbound WS payload. Logs a console warning
 * when the payload is not an object or lacks a required key, then
 * returns null so the caller's null-check short-circuits. Shape
 * enforcement is minimal — only the keys that discriminate which
 * handler should process the message.
 */
export const parseInboundPayload = <T,>(
  raw: unknown,
  channel: string,
  options: InboundPayloadOptions = { requireRequestId: true },
): T | null => {
  const parsed = parseJson<Record<string, unknown>>(raw)
  if (parsed === null) {
    return null
  }
  if (options.requireRequestId && typeof parsed.request_id !== 'string') {
    console.warn(
      `[${channel}] malformed payload — missing or non-string request_id`,
      parsed,
    )
    return null
  }
  if (options.requireType && typeof parsed.type !== 'string') {
    console.warn(
      `[${channel}] malformed payload — missing or non-string type`,
      parsed,
    )
    return null
  }
  return parsed as T
}
