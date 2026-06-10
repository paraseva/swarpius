export interface GuidanceEntry {
  id: string
  title: string
  content: string
  docFile: string
  devOnly: boolean
  /** Section is only relevant to the desktop bundle (e.g. stop-marker
   *  setup, which needs the agent and browser on the same machine).
   *  Consumers gate it on the `is_bundle` feature-availability flag. */
  bundleOnly: boolean
}

const HEADING_RE = /^(#{2,})\s+(.+?)\s+\{#([a-z0-9-]+)\}\s*$/
const END_GUIDANCE_RE = /^<!--\s*end-guidance\s*-->$/
const AUDIENCE_DEV_RE = /^<!--\s*audience:\s*dev\s*-->$/
const AUDIENCE_BUNDLE_RE = /^<!--\s*audience:\s*bundle\s*-->$/

/**
 * Parse a markdown document into guidance sections keyed by their {#id}.
 *
 * Sections are delimited by ## (or ###, etc.) headings that include a
 * `{#id}` suffix.  Content is extracted from the line after the heading
 * to the `<!-- end-guidance -->` marker, or to the next same-level-or-higher
 * heading if no marker is present.
 */
export function parseGuidanceSections(
  markdown: string,
  docFile: string,
): Record<string, GuidanceEntry> {
  const lines = markdown.split('\n')
  const result: Record<string, GuidanceEntry> = {}

  let i = 0
  while (i < lines.length) {
    const match = HEADING_RE.exec(lines[i])
    if (!match) {
      i++
      continue
    }

    const headingLevel = match[1].length
    const title = match[2].trim()
    const id = match[3]
    i++

    // Check for <!-- audience: dev|bundle --> within the section
    let devOnly = false
    let bundleOnly = false
    const contentLines: string[] = []
    let foundMarker = false

    while (i < lines.length) {
      const line = lines[i]

      // Check for end-guidance marker
      if (END_GUIDANCE_RE.test(line.trim())) {
        foundMarker = true
        i++
        break
      }

      // Check for a same-level or higher heading (## or #) — stop here
      const nextHeading = /^(#{1,6})\s/.exec(line)
      if (nextHeading && nextHeading[1].length <= headingLevel) {
        break
      }

      // Detect audience flags
      if (AUDIENCE_DEV_RE.test(line.trim())) {
        devOnly = true
        i++
        continue
      }
      if (AUDIENCE_BUNDLE_RE.test(line.trim())) {
        bundleOnly = true
        i++
        continue
      }

      contentLines.push(line)
      i++
    }

    // If we hit end-guidance, skip remaining content until next heading
    if (foundMarker) {
      while (i < lines.length && !/^#{1,6}\s/.test(lines[i])) {
        i++
      }
    }

    result[id] = {
      id,
      title,
      content: contentLines.join('\n').trim(),
      docFile,
      devOnly,
      bundleOnly,
    }
  }

  return result
}
