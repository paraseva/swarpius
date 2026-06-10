import { describe, it, expect } from 'vitest'
import { parseGuidanceSections, type GuidanceEntry } from './parseGuidanceSections'

const guideMd = `# Main Title

Some intro text.

## Getting Started {#getting-started}

Welcome to Swarpius. Here's how to begin.

<!-- end-guidance -->

More detail that is NOT part of the guidance excerpt.

## Chat Basics {#chat-basics}

Talk to Swarpius naturally. Examples:

- "Play some jazz"
- "What's playing?"

<!-- end-guidance -->

### Tips

These tips are below the marker, so not included.

## No ID Section

This section has no {#id} so it should be skipped.

## Diagnostics {#diagnostics}

<!-- audience: dev -->

Dev-only diagnostics info here.

<!-- end-guidance -->

## Stop Setup {#stop-setup}

<!-- audience: bundle -->

Bundle-only setup steps here.

<!-- end-guidance -->

## Short Section {#short-section}

This section has no end-guidance marker, so the full content up to the next heading is the excerpt.

## Another {#another}

Final section content, no marker. Goes to end of file.
`

describe('parseGuidanceSections', () => {
  it('extracts sections with {#id} markers', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['getting-started']).toBeDefined()
    expect(result['chat-basics']).toBeDefined()
  })

  it('uses content up to <!-- end-guidance --> marker', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const section = result['getting-started']
    expect(section.content).toContain("Welcome to Swarpius")
    expect(section.content).not.toContain('NOT part of the guidance')
  })

  it('extracts heading text without the {#id} suffix', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['getting-started'].title).toBe('Getting Started')
    expect(result['chat-basics'].title).toBe('Chat Basics')
  })

  it('records the source file', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['getting-started'].docFile).toBe('guide')
  })

  it('skips headings without {#id}', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const ids = Object.keys(result)
    expect(ids).not.toContain('no-id-section')
    // Verify it didn't create an entry for the "No ID Section" heading
    for (const entry of Object.values(result)) {
      expect(entry.title).not.toBe('No ID Section')
    }
  })

  it('detects <!-- audience: dev --> as devOnly', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['diagnostics'].devOnly).toBe(true)
  })

  it('marks non-dev sections as devOnly: false', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['getting-started'].devOnly).toBe(false)
    expect(result['chat-basics'].devOnly).toBe(false)
  })

  it('detects <!-- audience: bundle --> as bundleOnly', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['stop-setup'].bundleOnly).toBe(true)
    expect(result['stop-setup'].content).toContain('Bundle-only setup steps')
  })

  it('marks non-bundle sections as bundleOnly: false', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    expect(result['getting-started'].bundleOnly).toBe(false)
    expect(result['diagnostics'].bundleOnly).toBe(false)
  })

  it('uses full section content when no end-guidance marker is present', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const section = result['short-section']
    expect(section.content).toContain('no end-guidance marker')
    // Should stop before the next ## heading
    expect(section.content).not.toContain('Final section content')
  })

  it('handles the last section without a marker (content to end of file)', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const section = result['another']
    expect(section.content).toContain('Final section content')
  })

  it('returns correct GuidanceEntry shape', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const entry: GuidanceEntry = result['chat-basics']
    expect(entry).toEqual({
      id: 'chat-basics',
      title: 'Chat Basics',
      content: expect.any(String),
      docFile: 'guide',
      devOnly: false,
      bundleOnly: false,
    })
  })

  it('returns empty record for markdown with no {#id} headings', () => {
    const noIds = `# Title\n\n## Section One\n\nSome content.\n\n## Section Two\n\nMore content.\n`
    const result = parseGuidanceSections(noIds, 'empty')
    expect(Object.keys(result)).toHaveLength(0)
  })

  it('returns empty record for empty string', () => {
    const result = parseGuidanceSections('', 'empty')
    expect(Object.keys(result)).toHaveLength(0)
  })

  it('trims whitespace from extracted content', () => {
    const result = parseGuidanceSections(guideMd, 'guide')
    const content = result['getting-started'].content
    expect(content).not.toMatch(/^\s/)
    expect(content).not.toMatch(/\s$/)
  })

  it('handles ### sub-headings with {#id}', () => {
    const md = `## Parent

Intro.

### Sub Section {#sub-section}

Sub content here.

<!-- end-guidance -->

More stuff.
`
    const result = parseGuidanceSections(md, 'test')
    expect(result['sub-section']).toBeDefined()
    expect(result['sub-section'].title).toBe('Sub Section')
    expect(result['sub-section'].content).toContain('Sub content here')
  })

  it('stops full-section extraction at same-level or higher heading', () => {
    const md = `## First {#first}

First content.

### Sub heading

Still part of first.

## Second {#second}

Second content.
`
    const result = parseGuidanceSections(md, 'test')
    expect(result['first'].content).toContain('First content')
    expect(result['first'].content).toContain('Still part of first')
    expect(result['first'].content).not.toContain('Second content')
  })
})
