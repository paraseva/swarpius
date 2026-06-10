import React from 'react'
import s from './AnalysisBrowser.module.css'

interface ParsedSkill {
  name: string
  description: string
  instructions: string
}

interface PromptSection {
  title: string
  content: string
  skills?: ParsedSkill[]
}

interface PromptGroup {
  label: string
  sections: PromptSection[]
}

const CONTEXT_PROVIDER_TITLES = new Set([
  'Current Date', 'Current Time', 'Zone Aliases', 'Zone Status',
  'Execution Trace', 'Search History', 'Conversation History',
])

function parsePromptSections(raw: string): PromptGroup[] {
  // Split into flat sections by ## headers, but not inside <skill> blocks
  // (skill instructions contain their own ## headers that shouldn't split sections)
  const flatSections: PromptSection[] = []
  const lines = raw.split('\n')
  let currentTitle = 'Preamble'
  let currentLines: string[] = []
  let insideSkill = 0

  for (const line of lines) {
    if (line.trim().startsWith('<skill>')) insideSkill++
    if (line.trim().startsWith('</skill>')) insideSkill--

    if (line.startsWith('## ') && insideSkill <= 0) {
      if (currentLines.length > 0 || currentTitle === 'Preamble') {
        flatSections.push({ title: currentTitle, content: currentLines.join('\n').trim() })
      }
      currentTitle = line.slice(3).trim()
      currentLines = []
    } else {
      currentLines.push(line)
    }
  }
  if (currentLines.length > 0) {
    flatSections.push({ title: currentTitle, content: currentLines.join('\n').trim() })
  }

  // Parse skill blocks within any section that contains <skill> tags
  const parsed = flatSections.map((section) => {
    if (!section.content.includes('<skill>')) return section
    const skills: ParsedSkill[] = []
    const skillRegex = /<skill>([\s\S]*?)<\/skill>/g
    let match: RegExpExecArray | null = null
    while ((match = skillRegex.exec(section.content)) !== null) {
      const block = match[1]
      const nameMatch = /<name>(.*?)<\/name>/.exec(block)
      const descMatch = /<description>([\s\S]*?)<\/description>/.exec(block)
      const instrMatch = /<instructions>([\s\S]*?)<\/instructions>/.exec(block)
      skills.push({
        name: nameMatch?.[1] ?? 'unknown',
        description: descMatch?.[1]?.trim() ?? '',
        instructions: instrMatch?.[1]?.trim() ?? '',
      })
    }
    const outsideSkills = section.content
      .replace(/<skill>[\s\S]*?<\/skill>/g, '')
      .replace(/<available_skills>|<\/available_skills>/g, '')
      .trim()
    return { ...section, content: outsideSkills, skills }
  })

  // Group sections into categories, preserving the order in which each
  // category first appears. This way the displayed order matches the
  // actual order the coordinator saw — historical logs retain their own
  // ordering even if the server-side section order is changed later.
  const categoryFor = (title: string): string => {
    if (title === 'Skill Definitions') return 'Skill Definitions'
    if (title === 'Key Rules') return 'Key Rules'
    if (CONTEXT_PROVIDER_TITLES.has(title)) return 'Context Providers'
    return 'Base Prompt'
  }

  const groupMap = new Map<string, PromptSection[]>()
  const groupOrder: string[] = []
  for (const section of parsed) {
    const label = categoryFor(section.title)
    if (!groupMap.has(label)) {
      groupMap.set(label, [])
      groupOrder.push(label)
    }
    groupMap.get(label)!.push(section)
  }
  return groupOrder.map((label) => ({ label, sections: groupMap.get(label)! }))
}

export const Chevron: React.FC<{ expanded: boolean }> = ({ expanded }) => (
  <svg
    className={`${s.analysisFindingChevron} ${expanded ? s.expanded : ''}`}
    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round"
  >
    <polyline points="9 18 15 12 9 6" />
  </svg>
)

const PromptSectionItem: React.FC<{ title: string; content: string; defaultOpen?: boolean }> = ({ title, content, defaultOpen = false }) => {
  const [expanded, setExpanded] = React.useState(defaultOpen)
  if (!content) return null
  return (
    <div className={s.promptSection}>
      <button type="button" className={s.promptSectionHeader} onClick={() => setExpanded(!expanded)}>
        <Chevron expanded={expanded} />
        <span className={s.promptSectionTitle}>{title}</span>
        <span className={s.promptSectionSize}>{content.length} chars</span>
      </button>
      {expanded && <pre className={`${s.rqLogPre} ${s.promptSectionBody}`}>{content}</pre>}
    </div>
  )
}

const SkillItem: React.FC<{ skill: ParsedSkill }> = ({ skill }) => {
  const [expanded, setExpanded] = React.useState(false)
  const charCount = skill.description.length + skill.instructions.length
  return (
    <div className={`${s.promptSection} ${s.promptSkillNested}`}>
      <button type="button" className={s.promptSectionHeader} onClick={() => setExpanded(!expanded)}>
        <Chevron expanded={expanded} />
        <span className={s.promptSectionTitle}>{skill.name}</span>
        <span className={s.promptSectionSize}>{charCount} chars</span>
      </button>
      {expanded && (
        <div className={s.promptSectionBody}>
          {skill.description && (
            <div className={s.promptSkillField}>
              <div className={s.promptSkillFieldLabel}>Description</div>
              <pre className={s.rqLogPre}>{skill.description}</pre>
            </div>
          )}
          {skill.instructions && (
            <div className={s.promptSkillField}>
              <div className={s.promptSkillFieldLabel}>Instructions</div>
              <pre className={s.rqLogPre}>{skill.instructions}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const PromptGroupItem: React.FC<{ group: PromptGroup }> = ({ group }) => {
  const [expanded, setExpanded] = React.useState(false)
  const totalChars = group.sections.reduce((sum, s) => {
    const skillChars = s.skills?.reduce((ss, sk) => ss + sk.description.length + sk.instructions.length, 0) ?? 0
    return sum + s.content.length + skillChars
  }, 0)
  return (
    <div className={s.promptSection}>
      <button type="button" className={`${s.promptSectionHeader} ${s.promptGroupHeader}`} onClick={() => setExpanded(!expanded)}>
        <Chevron expanded={expanded} />
        <span className={s.promptSectionTitle}>{group.label}</span>
        <span className={s.promptSectionSize}>{totalChars} chars</span>
      </button>
      {expanded && (
        <div className={`${s.promptSectionBody} ${s.promptGroupBody}`}>
          {group.sections.map((section) => (
            <div key={section.title}>
              <PromptSectionItem title={section.title} content={section.content} />
              {section.skills?.map((skill) => (
                <SkillItem key={skill.name} skill={skill} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export const PromptViewer: React.FC<{ name: string; content: string }> = ({ name, content }) => {
  const [expanded, setExpanded] = React.useState(false)
  const groups = React.useMemo(() => parsePromptSections(content), [content])
  return (
    <div className={s.rqLogTool}>
      <button
        type="button"
        className={s.rqLogToolHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={s.rqLogToolSkill}>{name}</span>
        <span className={s.rqLogToolDuration}>{groups.length} groups</span>
        <Chevron expanded={expanded} />
      </button>
      {expanded && (
        <div className={s.rqLogToolBody}>
          {groups.map((group) => (
            <PromptGroupItem key={group.label} group={group} />
          ))}
        </div>
      )}
    </div>
  )
}
