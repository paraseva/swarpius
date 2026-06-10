export interface TextSegment {
  type: 'text' | 'request-id' | 'result-handle'
  value: string
}

const REFERENCE_REGEX = /(rq-c\d{2}-\d{4}|(?:res|que)_\d{5})/g

/**
 * Parse text for request ID (rq-cNN-NNNN) and result handle (res_NNNNN)
 * references, returning an array of typed segments.
 */
export function parseReferences(text: string): TextSegment[] {
  const segments: TextSegment[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  REFERENCE_REGEX.lastIndex = 0
  while ((match = REFERENCE_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', value: text.slice(lastIndex, match.index) })
    }
    const value = match[1]
    segments.push({
      type: value.startsWith('rq-') ? 'request-id' : 'result-handle',
      value,
    })
    lastIndex = REFERENCE_REGEX.lastIndex
  }
  if (lastIndex < text.length) {
    segments.push({ type: 'text', value: text.slice(lastIndex) })
  }
  return segments
}
