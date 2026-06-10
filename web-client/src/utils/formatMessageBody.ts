import { parseDetailsMarkup, type DetailSegment } from './parseDetailsMarkup'

export type { DetailSegment }

export interface ParsedMessageBody {
  source: string | null
  content: string
  segments: DetailSegment[] | null
  parsedJson: unknown | null
  hasPlanField: boolean
  // True only for inbound Swarpius chat responses (channel='chat' with
  // a structured ``chat_response`` payload). Gates markdown rendering
  // in FormattedMessageBody — outbound user text and diagnostics
  // channels stay verbatim.
  isChatResponse: boolean
}

const CHAT_LEAK_KEYS = [
  'awaiting_user_response',
  'selected_skill',
  'tool_parameters',
  'problem_description',
  'detailed_information',
] as const

const CHAT_LEAK_MARKER_PATTERN = new RegExp(
  `"?(${CHAT_LEAK_KEYS.join('|')})"?\\s*(?::|=|>)`,
  'i',
)
const CHAT_RESPONSE_JSON_PATTERN = /"?chat_response"?\s*:\s*"(?<value>(?:[^"\\]|\\.)*)"/i

const sanitiseChatMessageContent = (rawContent: string): string => {
  let cleaned = rawContent.trim()
  if (!cleaned) return cleaned

  cleaned = cleaned.replace(/\\"/g, '"').replace(/\\n/g, '\n')

  try {
    const parsed = JSON.parse(cleaned) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const payload = parsed as Record<string, unknown>
      const chatValue = payload.chat_response
      if (typeof chatValue === 'string' && chatValue.trim()) {
        return chatValue.trim()
      }
    }
  } catch {
    // Keep fallback regex cleanup below.
  }

  const markerMatch = CHAT_LEAK_MARKER_PATTERN.exec(cleaned)
  if (markerMatch) {
    const prefix = cleaned.slice(0, markerMatch.index).replace(/[ \t\r\n,;:{[<"']+$/g, '')
    if (prefix) {
      return prefix
    }
    const extracted = CHAT_RESPONSE_JSON_PATTERN.exec(cleaned)
    if (extracted?.groups?.value) {
      return extracted.groups.value.replace(/\\"/g, '"').replace(/\\n/g, '\n').trim()
    }
  }

  return cleaned
}

const inferToolName = (parsedJson: unknown): string | null => {
  if (!parsedJson || typeof parsedJson !== 'object' || Array.isArray(parsedJson)) {
    return null
  }

  const payload = parsedJson as Record<string, unknown>

  if (Array.isArray(payload.results)) {
    return 'Searxng Search Tool'
  }
  if (Array.isArray(payload.items) && typeof payload.description === 'string') {
    return 'Roon Search Tool'
  }
  if (Array.isArray(payload.items) && typeof payload.result_handle === 'string') {
    return 'Result Fetch Tool'
  }
  if (
    typeof payload.operation === 'string' &&
    (typeof payload.status === 'object' || Array.isArray(payload.zones))
  ) {
    return 'Roon Status Tool'
  }
  if (typeof payload.zone === 'string' && typeof payload.result === 'string') {
    return 'Roon Action Tool'
  }
  if (typeof payload.result === 'string' && 'zone' in payload === false) {
    return 'Roon Config Tool'
  }

  return null
}

const appendToolNameToSource = (source: string | null, parsedJson: unknown, channel?: string): string | null => {
  if (
    !source ||
    channel !== 'tool-outputs' ||
    (!source.toLowerCase().includes('tool output') && !source.toLowerCase().includes('tool input'))
  ) {
    return source
  }

  const inferredToolName = inferToolName(parsedJson)
  if (!inferredToolName || source.includes(inferredToolName)) {
    return source
  }

  return source.replace(/\btool (output|input)\]$/i, `${inferredToolName} $1]`)
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export const parseMessageBody = (body: string, channel?: string, payload?: unknown): ParsedMessageBody => {
  if (channel === 'chat' && isRecord(payload) && typeof payload.chat_response === 'string') {
    const content = sanitiseChatMessageContent(payload.chat_response || '')
    const segments = content.includes('<extended_info') || content.includes('<list>') || content.includes('<list ') ? parseDetailsMarkup(content) : null
    return {
      source: null,
      content,
      segments,
      parsedJson: null,
      hasPlanField: false,
      isChatResponse: true,
    }
  }

  if (channel !== 'chat' && (isRecord(payload) || Array.isArray(payload))) {
    const source = isRecord(payload) && typeof payload.source === 'string' ? payload.source : null
    const textContent = isRecord(payload) && typeof payload.text === 'string' ? payload.text.trim() : ''
    if (textContent) {
      let parsedFromText: unknown | null = null
      if (textContent.startsWith('{') || textContent.startsWith('[')) {
        try {
          parsedFromText = JSON.parse(textContent)
        } catch {
          parsedFromText = null
        }
      }
      const hasPlanField =
        parsedFromText !== null &&
        typeof parsedFromText === 'object' &&
        !Array.isArray(parsedFromText) &&
        'plan' in parsedFromText
      return {
        source,
        content: textContent,

        segments: null,
        parsedJson: parsedFromText,
        hasPlanField,
        isChatResponse: false,
      }
    }
    const hasPlanField = isRecord(payload) && 'plan' in payload
    return {
      source,
      content: textContent,

      segments: null,
      parsedJson: payload,
      hasPlanField,
      isChatResponse: false,
    }
  }

  const normalised = body.trimStart()
  if (!normalised.trim()) {
    return {
      source: null,
      content: '',

      segments: null,
      parsedJson: null,
      hasPlanField: false,
      isChatResponse: false,
    }
  }

  let source: string | null = null
  let content = normalised.trim()

  const firstNewline = normalised.indexOf('\n')
  if (firstNewline !== -1) {
    const firstLine = normalised.slice(0, firstNewline).trim()
    if (firstLine.startsWith('[') && firstLine.endsWith(']')) {
      source = firstLine
      content = normalised.slice(firstNewline + 1).trim()
    }
  }

  if (channel === 'chat') {
    content = sanitiseChatMessageContent(content)
  }

  let parsedJson: unknown | null = null
  if (channel !== 'chat' && (content.startsWith('{') || content.startsWith('['))) {
    try {
      parsedJson = JSON.parse(content)
    } catch {
      parsedJson = null
    }
  }

  const hasPlanField =
    parsedJson !== null &&
    typeof parsedJson === 'object' &&
    !Array.isArray(parsedJson) &&
    'plan' in parsedJson

  return {
    source: appendToolNameToSource(source, parsedJson, channel),
    content,
    segments: null,
    parsedJson,
    hasPlanField,
    isChatResponse: false,
  }
}
