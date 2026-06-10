import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WebSocketContext, type SocketMessage, type WebSocketContextValue } from '../websocketContext'
import { DefaultZoneBadge } from './DefaultZoneBadge'

const renderBadge = (zone: { zone_name: string | null; alias: string | null; group_name: string | null; is_grouped: boolean; is_online: boolean } | null) => {
  const ctx: WebSocketContextValue = {
    status: 'open',
    messages: [],
    sendMessage: () => '',
    isLlmActive: false,
    latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
    trimmedCount: 0,
  }
  return render(
    <WebSocketContext.Provider value={ctx}>
      <DefaultZoneBadge zone={zone} />
    </WebSocketContext.Provider>,
  )
}

describe('DefaultZoneBadge', () => {
  it('renders zone name without alias', () => {
    renderBadge({ zone_name: 'Living Room', alias: null, group_name: null, is_grouped: false, is_online: true })

    expect(screen.getByText('Default Zone')).toBeInTheDocument()
    expect(screen.getByText('Living Room')).toBeInTheDocument()
  })

  it('renders alias with zone name in parentheses', () => {
    renderBadge({ zone_name: 'Living Room', alias: 'Lounge', group_name: null, is_grouped: false, is_online: true })

    expect(screen.getByText('Lounge (Living Room)')).toBeInTheDocument()
  })

  it('renders group name with zone name in parentheses', () => {
    renderBadge({ zone_name: 'MDAC+ USB + 1', alias: null, group_name: 'Total', is_grouped: true, is_online: true })

    expect(screen.getByText('Total (MDAC+ USB + 1)')).toBeInTheDocument()
  })

  it('renders nothing when zone_name is null', () => {
    const { container } = renderBadge({ zone_name: null, alias: null, group_name: null, is_grouped: false, is_online: false })

    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when zone is null', () => {
    const { container } = renderBadge(null)

    expect(container.firstChild).toBeNull()
  })

  it('renders a chevron for the dropdown', () => {
    const { container } = renderBadge({ zone_name: 'Living Room', alias: null, group_name: null, is_grouped: false, is_online: true })

    const button = container.querySelector('button[title="Click to change default zone"]')
    expect(button).not.toBeNull()
    const svg = button!.querySelector('svg')
    expect(svg).toBeInTheDocument()
  })

  it('removes a rejected zone from the dropdown after a failed set-default', async () => {
    const sendMessage = vi.fn<(channel: string, body: string) => string>(() => '')
    const ctxValue = (msgs: SocketMessage[]): WebSocketContextValue => ({
      status: 'open',
      messages: msgs,
      sendMessage,
      isLlmActive: false,
      latestZoneSnapshot: null,
    connectionGeneration: 0,
    isRestarting: false,
    markRestarting: () => {},
      trimmedCount: 0,
    })

    const zone = { zone_name: 'Lounge', alias: null, group_name: null, is_grouped: false, is_online: true }
    const { rerender } = render(
      <WebSocketContext.Provider value={ctxValue([])}>
        <DefaultZoneBadge zone={zone} />
      </WebSocketContext.Provider>,
    )

    const user = userEvent.setup()
    await user.click(screen.getByTitle('Click to change default zone'))

    const listRequest = JSON.parse(sendMessage.mock.calls.at(-1)![1])
    expect(listRequest.action).toBe('list_zones')

    const zonesPayload = [
      { display_name: 'Lounge', zone_alias: null, group_name: null, state: 'playing', is_default: true, is_grouped: false, group_members: [] },
      { display_name: 'Kitchen', zone_alias: null, group_name: null, state: 'stopped', is_default: false, is_grouped: false, group_members: [] },
      { display_name: 'Study', zone_alias: null, group_name: null, state: 'stopped', is_default: false, is_grouped: false, group_members: [] },
    ]
    const listResp: SocketMessage = {
      id: 'm1',
      channel: 'roon-control-response',
      direction: 'inbound',
      body: JSON.stringify({ request_id: listRequest.request_id, ok: true, zones: zonesPayload }),
      payload: { request_id: listRequest.request_id, ok: true, zones: zonesPayload },
      timestamp: 1,
    }
    rerender(
      <WebSocketContext.Provider value={ctxValue([listResp])}>
        <DefaultZoneBadge zone={zone} />
      </WebSocketContext.Provider>,
    )

    const initialOptions = screen.getAllByRole('option').map((o) => o.textContent ?? '')
    expect(initialOptions.some((t) => t.includes('Kitchen'))).toBe(true)
    expect(initialOptions.some((t) => t.includes('Study'))).toBe(true)

    await user.click(screen.getByRole('option', { name: /Kitchen/ }))

    const setRequest = JSON.parse(sendMessage.mock.calls.at(-1)![1])
    expect(setRequest.action).toBe('set_default_zone')
    expect(setRequest.zone).toBe('Kitchen')

    const failResp: SocketMessage = {
      id: 'm2',
      channel: 'roon-control-response',
      direction: 'inbound',
      body: JSON.stringify({
        request_id: setRequest.request_id,
        ok: false,
        zone: 'Kitchen',
        error: "Unknown zone or alias 'Kitchen'.",
      }),
      payload: {
        request_id: setRequest.request_id,
        ok: false,
        zone: 'Kitchen',
        error: "Unknown zone or alias 'Kitchen'.",
      },
      timestamp: 2,
    }
    rerender(
      <WebSocketContext.Provider value={ctxValue([listResp, failResp])}>
        <DefaultZoneBadge zone={zone} />
      </WebSocketContext.Provider>,
    )

    const remainingOptions = screen.getAllByRole('option').map((o) => o.textContent ?? '')
    expect(remainingOptions.some((t) => t.includes('Kitchen'))).toBe(false)
    expect(remainingOptions.some((t) => t.includes('Study'))).toBe(true)
    expect(screen.getByText("Unknown zone or alias 'Kitchen'.")).toBeInTheDocument()
  })
})
