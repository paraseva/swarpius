import { describe, expect, it } from 'vitest'
import { parseReferences, type TextSegment } from './parseReferences'

describe('parseReferences', () => {
  it('returns single text segment for plain text', () => {
    const result = parseReferences('No references here')
    expect(result).toEqual([{ type: 'text', value: 'No references here' }])
  })

  it('parses a single request ID', () => {
    const result = parseReferences('See rq-c04-0001 for details')
    expect(result).toEqual([
      { type: 'text', value: 'See ' },
      { type: 'request-id', value: 'rq-c04-0001' },
      { type: 'text', value: ' for details' },
    ])
  })

  it('parses a single result handle', () => {
    const result = parseReferences('Used res_00001 from cache')
    expect(result).toEqual([
      { type: 'text', value: 'Used ' },
      { type: 'result-handle', value: 'res_00001' },
      { type: 'text', value: ' from cache' },
    ])
  })

  it('parses mixed references', () => {
    const result = parseReferences('rq-c04-0002 used res_00001 and res_00002')
    const types = result.map((s: TextSegment) => s.type)
    expect(types).toEqual(['request-id', 'text', 'result-handle', 'text', 'result-handle'])
    expect(result[0].value).toBe('rq-c04-0002')
    expect(result[2].value).toBe('res_00001')
    expect(result[4].value).toBe('res_00002')
  })

  it('handles adjacent references separated by space', () => {
    const result = parseReferences('rq-c04-0001 rq-c04-0002')
    expect(result).toEqual([
      { type: 'request-id', value: 'rq-c04-0001' },
      { type: 'text', value: ' ' },
      { type: 'request-id', value: 'rq-c04-0002' },
    ])
  })

  it('handles reference at start of text', () => {
    const result = parseReferences('rq-c01-0001 was clean')
    expect(result[0]).toEqual({ type: 'request-id', value: 'rq-c01-0001' })
  })

  it('handles reference at end of text', () => {
    const result = parseReferences('See rq-c01-0001')
    expect(result[result.length - 1]).toEqual({ type: 'request-id', value: 'rq-c01-0001' })
  })

  it('returns empty array for empty string', () => {
    const result = parseReferences('')
    expect(result).toEqual([])
  })
})
