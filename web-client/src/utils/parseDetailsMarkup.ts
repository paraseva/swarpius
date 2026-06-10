/** Segment from parsing `<extended_info>` and `<list>` markup in response text. */
export interface DetailSegment {
  type: 'text' | 'extended_info' | 'list'
  content: string
  summary?: string
  children?: DetailSegment[]
}

const EXTENDED_INFO_RE = /<extended_info[^>]*>([\s\S]*?)<\/extended_info>/gi
const SUMMARY_RE = /<summary[^>]*>([\s\S]*?)<\/summary>/i
const stripTags = (s: string) => s.replace(/<[^>]*>/g, '').trim()

/** Match an opening `<list>` tag, with or without attributes. */
const LIST_OPEN_RE = /<list(?:\s[^>]*)?>/

/** Find the next `<list>` or `<list ...>` opening tag from a given position. */
function findListOpen(text: string, startFrom: number): { index: number; tagLength: number } | null {
  const slice = text.slice(startFrom)
  const match = LIST_OPEN_RE.exec(slice)
  if (!match) return null
  return { index: startFrom + match.index, tagLength: match[0].length }
}

/**
 * Find the matching `</list>` for an opening `<list>` tag, handling nesting.
 * Returns the index of the start of the closing `</list>` tag, or -1.
 */
function findMatchingListClose(text: string, startAfter: number): number {
  let depth = 1
  let i = startAfter
  while (i < text.length) {
    const open = findListOpen(text, i)
    const closeIdx = text.indexOf('</list>', i)
    if (closeIdx === -1) return -1
    if (open && open.index < closeIdx) {
      depth++
      i = open.index + open.tagLength
    } else {
      depth--
      if (depth === 0) return closeIdx
      i = closeIdx + 7
    }
  }
  return -1
}

/** Check whether text contains a `<list>` or `<list ...>` opening tag. */
function hasList(text: string): boolean {
  return LIST_OPEN_RE.test(text)
}

/**
 * Parse `<list>` blocks from text, supporting nesting.
 * Returns an array of segments (text and list).
 */
function parseListBlocks(text: string): DetailSegment[] {
  const segments: DetailSegment[] = []
  let lastEnd = 0

  let searchFrom = 0
  while (searchFrom < text.length) {
    const open = findListOpen(text, searchFrom)
    if (!open) break

    const contentStart = open.index + open.tagLength
    const closeIdx = findMatchingListClose(text, contentStart)
    if (closeIdx === -1) break

    const before = text.slice(lastEnd, open.index).trim()
    if (before) segments.push({ type: 'text', content: before })

    let inner = text.slice(contentStart, closeIdx).trim()

    // Extract summary only if it appears before any nested <list> tag.
    // Otherwise the summary belongs to a child block and should not be
    // pulled up to this level.
    let summary: string | undefined
    const firstNestedOpen = findListOpen(inner, 0)
    const sumMatch = SUMMARY_RE.exec(inner)
    if (sumMatch && (!firstNestedOpen || sumMatch.index < firstNestedOpen.index)) {
      summary = sumMatch[1].trim() || undefined
      inner = (inner.slice(0, sumMatch.index) + inner.slice(sumMatch.index + sumMatch[0].length)).trim()
    }

    if (hasList(inner)) {
      const children = parseListBlocks(inner)
      segments.push({ type: 'list', content: '', summary, children })
    } else {
      const cleaned = stripTags(inner).trim()
      if (cleaned) segments.push({ type: 'list', content: cleaned, summary })
    }

    lastEnd = closeIdx + 7 // after '</list>'
    searchFrom = lastEnd
  }

  const trailing = text.slice(lastEnd).trim()
  if (trailing) segments.push({ type: 'text', content: trailing })

  return segments
}

/**
 * Parse text containing `<extended_info>`, `<summary>`, and `<list>` tags into ordered segments.
 * Text outside these blocks becomes `{type: 'text'}` segments.
 * Extended_info blocks become `{type: 'extended_info'}` with optional `summary`.
 * List blocks become `{type: 'list'}` with optional `summary` and `children`.
 */
export function parseDetailsMarkup(text: string): DetailSegment[] {
  // If the text contains <list> blocks, parse those first (they may be nested)
  const hasListBlocks = hasList(text)
  const hasExtendedInfo = EXTENDED_INFO_RE.test(text)
  EXTENDED_INFO_RE.lastIndex = 0 // reset after .test()

  if (!hasListBlocks && !hasExtendedInfo) {
    return [{ type: 'text', content: text }]
  }

  // Parse <extended_info> blocks first (they don't nest)
  const segments: DetailSegment[] = []
  let lastEnd = 0

  for (const match of text.matchAll(EXTENDED_INFO_RE)) {
    const before = text.slice(lastEnd, match.index).trim()
    if (before) {
      if (hasList(before)) {
        segments.push(...parseListBlocks(before))
      } else {
        segments.push({ type: 'text', content: before })
      }
    }

    let inner = match[1].trim()
    let summary: string | undefined
    const sumMatch = SUMMARY_RE.exec(inner)
    if (sumMatch) {
      summary = sumMatch[1].trim() || undefined
      inner = (inner.slice(0, sumMatch.index) + inner.slice(sumMatch.index + sumMatch[0].length)).trim()
    }
    const cleaned = stripTags(inner).trim()
    if (cleaned) segments.push({ type: 'extended_info', content: cleaned, summary })

    lastEnd = (match.index ?? 0) + match[0].length
  }

  const trailing = text.slice(lastEnd).trim()
  if (trailing) {
    if (hasList(trailing)) {
      segments.push(...parseListBlocks(trailing))
    } else {
      segments.push({ type: 'text', content: trailing })
    }
  }

  return segments.length > 0 ? segments : [{ type: 'text', content: text }]
}
