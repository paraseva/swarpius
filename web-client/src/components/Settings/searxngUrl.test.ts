import { describe, expect, it } from 'vitest'
import { parseSearxngUrl, combineSearxngUrl } from './searxngUrl'

describe('parseSearxngUrl', () => {
  it('splits a full http URL into scheme + rest', () => {
    expect(parseSearxngUrl('http://localhost:8888')).toEqual({
      scheme: 'http://',
      rest: 'localhost:8888',
    })
  })

  it('keeps an optional path on the rest, and normalises scheme case', () => {
    expect(parseSearxngUrl('HTTPS://example.com/searxng')).toEqual({
      scheme: 'https://',
      rest: 'example.com/searxng',
    })
  })

  it('reports no scheme when the input lacks one (so the dropdown keeps its value)', () => {
    expect(parseSearxngUrl('localhost:8888')).toEqual({ scheme: null, rest: 'localhost:8888' })
  })

  it('trims surrounding whitespace; empty stays empty', () => {
    expect(parseSearxngUrl('  http://x  ')).toEqual({ scheme: 'http://', rest: 'x' })
    expect(parseSearxngUrl('')).toEqual({ scheme: null, rest: '' })
  })
})

describe('combineSearxngUrl', () => {
  it('joins scheme + host', () => {
    expect(combineSearxngUrl('https://', 'example.com')).toBe('https://example.com')
  })

  it('returns empty (unset) when the host is blank', () => {
    expect(combineSearxngUrl('http://', '   ')).toBe('')
  })

  it('trims the host before joining', () => {
    expect(combineSearxngUrl('http://', '  host:8888  ')).toBe('http://host:8888')
  })
})
