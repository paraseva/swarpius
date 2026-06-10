import React from 'react'
import s from './JsonTreeView.module.css'

interface JsonTreeViewProps {
  data: unknown
  className?: string
}

export const JsonTreeView: React.FC<JsonTreeViewProps> = ({ data, className }) => (
  <div className={className ? `${s.root} ${className}` : s.root}>
    <ValueNode value={data} depth={0} />
  </div>
)

const COLLAPSE_DEPTH = 2

const ValueNode: React.FC<{ value: unknown; depth: number }> = ({ value, depth }) => {
  if (value === null) return <span className={s.null}>null</span>
  if (value === undefined) return <span className={s.null}>undefined</span>
  if (typeof value === 'boolean') return <span className={s.bool}>{String(value)}</span>
  if (typeof value === 'number') return <span className={s.num}>{String(value)}</span>
  if (typeof value === 'string') return <StringValue value={value} depth={depth} />
  if (Array.isArray(value)) return <ArrayNode items={value} depth={depth} />
  if (typeof value === 'object') return <ObjectNode obj={value as Record<string, unknown>} depth={depth} />
  return <span>{String(value)}</span>
}

function tryParseJson(value: string): unknown | undefined {
  if ((value.startsWith('{') || value.startsWith('[')) && value.length > 2) {
    try {
      const parsed = JSON.parse(value)
      if (typeof parsed === 'object' && parsed !== null) return parsed
    } catch { /* not JSON */ }
  }
  return undefined
}

const StringValue: React.FC<{ value: string; depth: number }> = ({ value, depth }) => {
  const embedded = tryParseJson(value)
  if (embedded !== undefined) {
    return (
      <>
        <span className={s.badge}>JSON</span>
        <ValueNode value={embedded} depth={depth} />
      </>
    )
  }

  if (value.includes('\n')) {
    return <pre className={s.multiline}>{value}</pre>
  }

  return <span className={s.str}>&quot;{value}&quot;</span>
}

const ObjectNode: React.FC<{ obj: Record<string, unknown>; depth: number }> = ({ obj, depth }) => {
  const keys = Object.keys(obj)
  const [collapsed, setCollapsed] = React.useState(depth >= COLLAPSE_DEPTH)

  if (keys.length === 0) return <span className={s.bracket}>{'{}'}</span>

  const toggle = () => setCollapsed((c) => !c)

  return collapsed ? (
    <button type="button" className={s.toggle} onClick={toggle} aria-expanded={false}>
      <span className={s.arrow}>&#9656;</span>
      <span className={s.summary}>{`{${keys.length}}`}</span>
    </button>
  ) : (
    <>
      <button type="button" className={s.toggle} onClick={toggle} aria-expanded={true}>
        <span className={`${s.arrow} ${s.arrowExpanded}`}>&#9656;</span>
      </button>
      <div className={s.children}>
        {keys.map((key) => (
          <div key={key}>
            <span className={s.key}>{key}</span>
            <span className={s.sep}>: </span>
            <ValueNode value={obj[key]} depth={depth + 1} />
          </div>
        ))}
      </div>
    </>
  )
}

const ArrayNode: React.FC<{ items: unknown[]; depth: number }> = ({ items, depth }) => {
  const [collapsed, setCollapsed] = React.useState(depth >= COLLAPSE_DEPTH)

  if (items.length === 0) return <span className={s.bracket}>{'[]'}</span>

  const toggle = () => setCollapsed((c) => !c)

  return collapsed ? (
    <button type="button" className={s.toggle} onClick={toggle} aria-expanded={false}>
      <span className={s.arrow}>&#9656;</span>
      <span className={s.summary}>{`[${items.length}]`}</span>
    </button>
  ) : (
    <>
      <button type="button" className={s.toggle} onClick={toggle} aria-expanded={true}>
        <span className={`${s.arrow} ${s.arrowExpanded}`}>&#9656;</span>
      </button>
      <div className={s.children}>
        {items.map((item, i) => (
          <div key={i}>
            <span className={s.idx}>{i}</span>
            <span className={s.sep}>: </span>
            <ValueNode value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    </>
  )
}
