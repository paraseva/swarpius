import { describe, expect, it } from 'vitest'
import { parseDetailsMarkup } from './parseDetailsMarkup'

describe('parseDetailsMarkup', () => {
  it('returns text segment for plain text', () => {
    const result = parseDetailsMarkup('Hello world')
    expect(result).toEqual([{ type: 'text', content: 'Hello world' }])
  })

  it('parses extended_info block', () => {
    const result = parseDetailsMarkup(
      '<extended_info><summary>Title</summary>\n1. Item A\n2. Item B\n</extended_info>',
    )
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('extended_info')
    expect(result[0].summary).toBe('Title')
    expect(result[0].content).toContain('Item A')
  })

  it('parses single list block', () => {
    const result = parseDetailsMarkup(
      '<list><summary>Album (2 items)</summary>\n\n1. Track A\n2. Track B\n</list>',
    )
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('list')
    expect(result[0].summary).toBe('Album (2 items)')
    expect(result[0].content).toContain('Track A')
    expect(result[0].content).toContain('Track B')
  })

  it('parses nested list blocks for multi-disc', () => {
    const text = [
      '<list><summary>Album (3 tracks, 2 discs)</summary>',
      '',
      '<list><summary>Disc 1 (2 tracks)</summary>',
      '',
      '1. Track A',
      '2. Track B',
      '</list>',
      '',
      '<list><summary>Disc 2 (1 track)</summary>',
      '',
      '1. Track C',
      '</list>',
      '',
      '</list>',
    ].join('\n')

    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(1)

    const outer = result[0]
    expect(outer.type).toBe('list')
    expect(outer.summary).toBe('Album (3 tracks, 2 discs)')
    expect(outer.children).toBeDefined()
    expect(outer.children).toHaveLength(2)

    const disc1 = outer.children![0]
    expect(disc1.type).toBe('list')
    expect(disc1.summary).toBe('Disc 1 (2 tracks)')
    expect(disc1.content).toContain('Track A')
    expect(disc1.content).toContain('Track B')

    const disc2 = outer.children![1]
    expect(disc2.type).toBe('list')
    expect(disc2.summary).toBe('Disc 2 (1 track)')
    expect(disc2.content).toContain('Track C')
  })

  it('handles text before and after list block', () => {
    const text = 'Here are the tracks:\n\n<list><summary>Album</summary>\n\n1. Track\n</list>\n\nEnjoy!'
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(3)
    expect(result[0].type).toBe('text')
    expect(result[0].content).toContain('tracks')
    expect(result[1].type).toBe('list')
    expect(result[2].type).toBe('text')
    expect(result[2].content).toContain('Enjoy')
  })

  it('handles mixed extended_info and list blocks', () => {
    const text = [
      '<extended_info><summary>Info</summary>Some details</extended_info>',
      '',
      '<list><summary>Tracks</summary>',
      '1. Track A',
      '</list>',
    ].join('\n')

    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(2)
    expect(result[0].type).toBe('extended_info')
    expect(result[1].type).toBe('list')
  })

  // ── <list> with attributes ──

  it('parses list block with ref and title attributes', () => {
    const text = '<list ref="res_00006" title="Queue on Headphones">\n(1) [bfb97] Light My Fire | Clubland 90s | Club House\n(2) [a00dc] Show Me Love | Clubland 90s | Robin S\n</list>'
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('list')
    expect(result[0].content).toContain('Light My Fire')
    expect(result[0].content).toContain('Show Me Love')
  })

  it('parses list block with only ref attribute', () => {
    const text = '<list ref="res_00001">\n1. Track A\n2. Track B\n</list>'
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('list')
    expect(result[0].content).toContain('Track A')
  })

  it('parses list with attributes alongside plain list', () => {
    const text = [
      '<list><summary>Album</summary>',
      '1. Track A',
      '</list>',
      '',
      '<list ref="res_00002" title="Queue">',
      '(1) [abc12] Track B',
      '</list>',
    ].join('\n')
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(2)
    expect(result[0].type).toBe('list')
    expect(result[0].summary).toBe('Album')
    expect(result[1].type).toBe('list')
    expect(result[1].content).toContain('Track B')
  })

  it('handles nested list where outer has attributes', () => {
    const text = [
      '<list ref="res_00001" title="Album">',
      '<list><summary>Disc 1</summary>',
      '1. Track A',
      '</list>',
      '</list>',
    ].join('\n')
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('list')
    expect(result[0].children).toBeDefined()
    expect(result[0].children).toHaveLength(1)
    expect(result[0].children![0].summary).toBe('Disc 1')
  })

  it('preserves text around list with attributes', () => {
    const text = 'Here is the queue:\n\n<list ref="res_00006" title="Queue">\n(1) Track A\n</list>\n\nEnjoy!'
    const result = parseDetailsMarkup(text)
    expect(result).toHaveLength(3)
    expect(result[0].type).toBe('text')
    expect(result[0].content).toContain('queue')
    expect(result[1].type).toBe('list')
    expect(result[2].type).toBe('text')
    expect(result[2].content).toContain('Enjoy')
  })
})
