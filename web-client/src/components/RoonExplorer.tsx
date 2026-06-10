import React from 'react'
import { useWebSocket } from '../websocketContext'
import s from './RoonExplorer.module.css'

interface RoonExplorerProps {
  onClose?: () => void
}

interface PathStep {
  itemKey: string | null
  title: string
  hint: string
}

interface ExplorerResponse {
  request_id?: string
  ok: boolean
  action?: 'search' | 'navigate' | 'up'
  browse?: unknown
  load?: unknown
  error?: string
}

interface RoonItem {
  item_key?: string
  title?: string
  subtitle?: string
  hint?: string
}

interface RoonList {
  title?: string
  subtitle?: string
  hint?: string
  count?: number
  level?: number
}

interface RoonLoad {
  list?: RoonList
  items?: RoonItem[]
}

function colourForHint(hint: string, hasSubtitle: boolean): string {
  if (hint === 'search') return s.stepSearch
  if (hint === 'action_list') return s.stepActionList
  if (hint === 'action') return s.stepAction
  if (hint === 'static' || hint === 'header') return s.stepStatic
  if (hint === 'list') return hasSubtitle ? s.stepListItem : s.stepListCategory
  return s.stepUnknown
}

function newRequestId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export const RoonExplorer: React.FC<RoonExplorerProps> = ({ onClose }) => {
  const { messages, sendMessage } = useWebSocket()
  const [searchText, setSearchText] = React.useState('')
  const [path, setPath] = React.useState<PathStep[]>([])
  const [browseResp, setBrowseResp] = React.useState<unknown>(null)
  const [loadResp, setLoadResp] = React.useState<unknown>(null)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const pendingRef = React.useRef<{
    requestId: string
    action: 'search' | 'navigate' | 'up'
    appendStep?: PathStep
    searchTerm?: string
  } | null>(null)
  const processedToRef = React.useRef<number>(-1)

  React.useEffect(() => {
    const startIdx = processedToRef.current + 1
    for (let i = startIdx; i < messages.length; i += 1) {
      const msg = messages[i]
      if (msg.direction !== 'inbound') continue
      if (msg.channel !== 'roon-explorer-response') continue
      let payload: ExplorerResponse | null = null
      if (msg.payload && typeof msg.payload === 'object') {
        payload = msg.payload as ExplorerResponse
      } else {
        try { payload = JSON.parse(msg.body) as ExplorerResponse } catch { /* ignore */ }
      }
      if (!payload) continue
      const pending = pendingRef.current
      if (!pending || payload.request_id !== pending.requestId) continue
      pendingRef.current = null
      setPending(false)
      if (!payload.ok) {
        setError(payload.error ?? 'Explorer request failed')
        continue
      }
      setError(null)
      setBrowseResp(payload.browse ?? null)
      setLoadResp(payload.load ?? null)
      if (pending.action === 'search') {
        setPath([{ itemKey: null, title: pending.searchTerm ?? '', hint: 'search' }])
      } else if (pending.action === 'navigate' && pending.appendStep) {
        setPath(prev => [...prev, pending.appendStep!])
      } else if (pending.action === 'up') {
        setPath(prev => prev.slice(0, -1))
      }
    }
    processedToRef.current = messages.length - 1
  }, [messages])

  const sendAction = React.useCallback(
    (action: 'search' | 'navigate' | 'up', extras: Record<string, unknown>, ctx?: { appendStep?: PathStep; searchTerm?: string }) => {
      const requestId = newRequestId()
      pendingRef.current = { requestId, action, ...ctx }
      setPending(true)
      setError(null)
      sendMessage('roon-explorer-request', JSON.stringify({
        request_id: requestId, action, ...extras,
      }))
    },
    [sendMessage],
  )

  const onSubmitSearch = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmed = searchText.trim()
    if (!trimmed || pending) return
    sendAction('search', { input: trimmed }, { searchTerm: trimmed })
  }

  const onItemClick = (item: RoonItem) => {
    if (pending || !item.item_key) return
    const step: PathStep = {
      itemKey: item.item_key,
      title: item.title ?? '(untitled)',
      hint: item.hint ?? '',
    }
    sendAction('navigate', { item_key: item.item_key }, { appendStep: step })
  }

  const onUp = () => {
    if (pending || path.length <= 1) return
    sendAction('up', {})
  }

  const load = (loadResp ?? null) as RoonLoad | null
  const items = Array.isArray(load?.items) ? load!.items! : []

  return (
    <div className={s.root}>
      <header className={s.header}>
        <h2 className={s.title}>Roon API Explorer</h2>
        {onClose && (
          <button type="button" className={s.closeButton} onClick={onClose} aria-label="Close Roon Explorer">×</button>
        )}
      </header>

      <form className={s.searchBar} onSubmit={onSubmitSearch}>
        <input
          type="text"
          className={s.searchInput}
          placeholder="Search Roon (artist, album, track…)"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          disabled={pending}
        />
        <button type="submit" className={s.button} disabled={pending || !searchText.trim()}>
          Search
        </button>
        <button type="button" className={s.button} onClick={onUp} disabled={pending || path.length <= 1}>
          Up
        </button>
      </form>

      <div className={s.legend}>
        <span><span className={`${s.legendChip} ${s.stepSearch}`} /> search term</span>
        <span><span className={`${s.legendChip} ${s.stepListCategory}`} /> category list</span>
        <span><span className={`${s.legendChip} ${s.stepListItem}`} /> item list</span>
        <span><span className={`${s.legendChip} ${s.stepActionList}`} /> action list</span>
        <span><span className={`${s.legendChip} ${s.stepAction}`} /> action</span>
        <span><span className={`${s.legendChip} ${s.stepStatic}`} /> static / header</span>
        <span><span className={`${s.legendChip} ${s.stepUnknown}`} /> other</span>
      </div>

      {path.length > 0 && (
        <div className={s.path} aria-label="Browse path">
          {path.map((step, idx) => {
            const isItem = idx > 0 && !!step.itemKey
            const cls = colourForHint(step.hint, isItem)
            return (
              <React.Fragment key={`${idx}-${step.itemKey ?? step.title}`}>
                {idx > 0 && <span className={s.pathSep}>/</span>}
                <span className={`${s.pathStep} ${cls}`} title={`hint: ${step.hint || '(none)'}`}>
                  {step.title}
                </span>
              </React.Fragment>
            )
          })}
        </div>
      )}

      {error && <div className={s.errorBanner}>Error: {error}</div>}

      <div className={s.responsePane}>
        {browseResp != null && (
          <details open className={s.responseSection}>
            <summary>browse_browse response</summary>
            <pre className={s.json}>{JSON.stringify(browseResp, null, 2)}</pre>
          </details>
        )}
        {loadResp != null && (
          <details open className={s.responseSection}>
            <summary>browse_load response</summary>
            <LoadResponseTree
              load={load}
              items={items}
              onItemClick={onItemClick}
              disabled={pending}
            />
          </details>
        )}
        {browseResp == null && loadResp == null && (
          <div className={s.placeholder}>
            Type a search and press Enter to begin. Every navigation step
            calls the raw Roon <code>browse_browse</code> + <code>browse_load</code>
            API pair with no Swarpius logic on the call path.
          </div>
        )}
      </div>
    </div>
  )
}

const LoadResponseTree: React.FC<{
  load: RoonLoad | null
  items: RoonItem[]
  onItemClick: (item: RoonItem) => void
  disabled: boolean
}> = ({ load, items, onItemClick, disabled }) => {
  if (!load) return null
  return (
    <div className={s.loadTree}>
      <div className={s.listMeta}>
        <span className={s.metaKey}>list:</span> <JsonValue value={load.list ?? null} />
      </div>
      <div className={s.itemsList}>
        <span className={s.metaKey}>items:</span>
        {items.length === 0 ? (
          <span className={s.metaEmpty}> []</span>
        ) : (
          <ol className={s.items}>
            {items.map((item, i) => (
              <li key={`${i}-${item.item_key ?? item.title ?? ''}`} className={s.item}>
                <ItemRow item={item} onClick={onItemClick} disabled={disabled} />
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  )
}

const ItemRow: React.FC<{
  item: RoonItem
  onClick: (item: RoonItem) => void
  disabled: boolean
}> = ({ item, onClick, disabled }) => {
  const hint = item.hint ?? ''
  const clickable = !!item.item_key && !disabled && hint !== 'action'
  const colour = colourForHint(hint, !!item.subtitle)
  return (
    <div className={s.itemRow}>
      <div className={s.itemHeader}>
        {clickable ? (
          <button
            type="button"
            className={`${s.itemTitleButton} ${colour}`}
            onClick={() => onClick(item)}
            title={`Navigate (item_key=${item.item_key})`}
          >
            {item.title ?? '(untitled)'}
          </button>
        ) : (
          <span className={`${s.itemTitle} ${colour}`}>{item.title ?? '(untitled)'}</span>
        )}
        {item.hint && <span className={s.itemHint}>{item.hint}</span>}
      </div>
      <pre className={s.itemJson}>{JSON.stringify(item, null, 2)}</pre>
    </div>
  )
}

const JsonValue: React.FC<{ value: unknown }> = ({ value }) => {
  if (value === null || value === undefined) return <span className={s.metaEmpty}>null</span>
  return <pre className={s.inlineJson}>{JSON.stringify(value, null, 2)}</pre>
}
