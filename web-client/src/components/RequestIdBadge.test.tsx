import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { RequestIdBadge } from './RequestIdBadge'
import { RequestFocusProvider } from '../RequestFocusProvider'
import { RequestFocusContext, type FocusedRequest } from '../requestFocusContext'

afterEach(cleanup)

function Probe({ onChange }: { onChange: (f: FocusedRequest | null) => void }) {
  return (
    <RequestFocusContext.Consumer>
      {(ctx) => {
        onChange(ctx?.focusedRequest ?? null)
        return null
      }}
    </RequestFocusContext.Consumer>
  )
}

describe('RequestIdBadge', () => {
  it('without syncKey, the badge is copy-only (no focus surface)', () => {
    let focused: FocusedRequest | null = null
    render(
      <RequestFocusProvider>
        <RequestIdBadge requestId="rq-c01-0001" />
        <Probe onChange={(f) => { focused = f }} />
      </RequestFocusProvider>,
    )
    fireEvent.click(screen.getByLabelText('Copy request ID rq-c01-0001'))
    expect(focused).toBeNull()
  })

  it('with syncKey, clicking the id focuses that request from this source', () => {
    let focused: FocusedRequest | null = null
    render(
      <RequestFocusProvider>
        <RequestIdBadge requestId="rq-c01-0001" syncKey="chat" />
        <Probe onChange={(f) => { focused = f }} />
      </RequestFocusProvider>,
    )
    fireEvent.click(screen.getByLabelText('Show request rq-c01-0001 in other panels'))
    expect(focused).toMatchObject({ requestId: 'rq-c01-0001', sourceKey: 'chat' })
  })

  it('re-clicking the same request re-fires (new nonce) so panels re-sync', () => {
    const nonces: Array<number | undefined> = []
    render(
      <RequestFocusProvider>
        <RequestIdBadge requestId="rq-c01-0001" syncKey="chat" />
        <Probe onChange={(f) => { nonces.push(f?.nonce) }} />
      </RequestFocusProvider>,
    )
    const id = screen.getByLabelText('Show request rq-c01-0001 in other panels')
    fireEvent.click(id)
    fireEvent.click(id)
    const seen = nonces.filter((n): n is number => typeof n === 'number')
    expect(new Set(seen).size).toBeGreaterThanOrEqual(2)
  })
})
