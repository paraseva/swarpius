/**
 * Strip markdown and non-speakable symbols from text before sending to TTS.
 */
export function sanitiseTtsText(text: string): string {
  if (!text) return ''

  let result = text

  result = result.replace(/^```\w*\n?/gm, '')
  result = result.replace(/^```$/gm, '')

  result = result.replace(/^#{1,6}\s+/gm, '')

  result = result.replace(/^[-*_]{3,}$/gm, '')

  result = result.replace(/^>\s?/gm, '')

  result = result.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')

  result = result.replace(/\*{3}(.+?)\*{3}/g, '$1')
  result = result.replace(/_{3}(.+?)_{3}/g, '$1')
  result = result.replace(/\*{2}(.+?)\*{2}/g, '$1')
  result = result.replace(/_{2}(.+?)_{2}/g, '$1')
  result = result.replace(/\*(.+?)\*/g, '$1')
  result = result.replace(/(?<!\w)_(.+?)_(?!\w)/g, '$1')

  result = result.replace(/`([^`]*)`/g, '$1')

  result = result.replace(/^[-*]\s+/gm, '')

  result = result.replace(/^\d+[.)]\s+/gm, '')

  // Arrow symbol → comma (common in alias listings)
  result = result.replace(/\s*→\s*/g, ', ')

  result = result.replace(/\n{3,}/g, '\n\n')

  return result.trim()
}
