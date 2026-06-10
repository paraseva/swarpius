import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AgentUnreachableModal } from './ConnectionStatusModal'

afterEach(cleanup)

describe('AgentUnreachableModal', () => {
  it('shows the agent checklist outside bundle mode', () => {
    render(<AgentUnreachableModal isBundle={false} />)
    expect(screen.getByText(/can't reach the Swarpius agent/i)).toBeTruthy()
    expect(screen.getByText(/Agent is running/i)).toBeTruthy()
  })

  it('tells bundle users to relaunch the app, without the agent checklist', () => {
    render(<AgentUnreachableModal isBundle />)
    expect(screen.getByText(/run the Swarpius app again/i)).toBeTruthy()
    expect(screen.queryByText(/Agent is running/i)).toBeNull()
  })
})
