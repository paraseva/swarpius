import { describe, expect, it } from 'vitest'
import { sanitiseTtsText } from './sanitiseTtsText'

describe('sanitiseTtsText', () => {
  it('strips bold markers', () => {
    expect(sanitiseTtsText('This is **bold** text')).toBe('This is bold text')
  })

  it('strips italic markers', () => {
    expect(sanitiseTtsText('This is *italic* text')).toBe('This is italic text')
  })

  it('strips bold+italic markers', () => {
    expect(sanitiseTtsText('This is ***bold italic*** text')).toBe('This is bold italic text')
  })

  it('strips inline code backticks', () => {
    expect(sanitiseTtsText('Run `npm install` now')).toBe('Run npm install now')
  })

  it('strips fenced code block markers', () => {
    expect(sanitiseTtsText('```js\nconsole.log("hi")\n```')).toBe('console.log("hi")')
  })

  it('strips bullet markers (dash)', () => {
    expect(sanitiseTtsText('- First item\n- Second item')).toBe('First item\nSecond item')
  })

  it('strips bullet markers (asterisk)', () => {
    expect(sanitiseTtsText('* First item\n* Second item')).toBe('First item\nSecond item')
  })

  it('strips numbered list markers', () => {
    expect(sanitiseTtsText('1. First\n2. Second\n10. Tenth')).toBe('First\nSecond\nTenth')
  })

  it('strips heading markers', () => {
    expect(sanitiseTtsText('## Heading\nSome text')).toBe('Heading\nSome text')
  })

  it('strips blockquote markers', () => {
    expect(sanitiseTtsText('> quoted text')).toBe('quoted text')
  })

  it('strips horizontal rules', () => {
    expect(sanitiseTtsText('Above\n---\nBelow')).toBe('Above\n\nBelow')
  })

  it('extracts link text from markdown links', () => {
    expect(sanitiseTtsText('Check [this link](https://example.com) out')).toBe('Check this link out')
  })

  it('handles arrow symbol from lists', () => {
    expect(sanitiseTtsText('**Lounge** → Living Room')).toBe('Lounge, Living Room')
  })

  it('collapses multiple blank lines', () => {
    expect(sanitiseTtsText('Line one\n\n\n\nLine two')).toBe('Line one\n\nLine two')
  })

  it('trims result', () => {
    expect(sanitiseTtsText('  hello  ')).toBe('hello')
  })

  it('returns empty string for empty input', () => {
    expect(sanitiseTtsText('')).toBe('')
  })

  it('handles combined markdown', () => {
    const input = '## Results\n\n- **Track 1** by *Artist A*\n- **Track 2** by *Artist B*\n\n> Enjoy!'
    const expected = 'Results\n\nTrack 1 by Artist A\nTrack 2 by Artist B\n\nEnjoy!'
    expect(sanitiseTtsText(input)).toBe(expected)
  })
})
