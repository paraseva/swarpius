import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FormattedMessageBody } from './FormattedMessageBody'

describe('FormattedMessageBody', () => {
  it('renders source label and plain text body', () => {
    render(<FormattedMessageBody body={'[Agent]\nHello world'} />)

    expect(screen.getByRole('heading', { level: 6, name: '[Agent]' })).toBeInTheDocument()
    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('renders json payload as tree view', () => {
    const { container } = render(<FormattedMessageBody body={'{"a":1,"b":"x"}'} />)
    const tree = container.querySelector('.message-tree')

    expect(tree).not.toBeNull()
    expect(screen.getByText('a')).toBeInTheDocument()
    expect(screen.getByText('b')).toBeInTheDocument()
  })

  it('renders sanitised chat text instead of leaked JSON payload', () => {
    render(
      <FormattedMessageBody
        body={
          '{"chat_response":"I found two versions. Which one do you want?","awaiting_user_response":true,"selected_skill":null}'
        }
        channel="chat"
      />,
    )

    expect(screen.getByText('I found two versions. Which one do you want?')).toBeInTheDocument()
  })

  it('renders structured chat payload chat_response', () => {
    render(<FormattedMessageBody body="fallback" channel="chat" payload={{ chat_response: 'Structured hi' }} />)

    expect(screen.getByText('Structured hi')).toBeInTheDocument()
  })

  it('renders summary as details header when provided in markup', () => {
    render(
      <FormattedMessageBody
        body="fallback"
        channel="chat"
        payload={{
          chat_response: 'Here are the tracks.\n\n<extended_info><summary>Favourites 2 (13 tracks)</summary>1. Track A\n2. Track B</extended_info>',
        }}
      />,
    )

    expect(screen.getByText('Favourites 2 (13 tracks)')).toBeInTheDocument()
    expect(screen.queryByText('Details')).not.toBeInTheDocument()
  })

  it('falls back to "Details" when no summary in markup', () => {
    render(
      <FormattedMessageBody
        body="fallback"
        channel="chat"
        payload={{
          chat_response: 'Results.\n\n<extended_info>1. Item A</extended_info>',
        }}
      />,
    )

    expect(screen.getByText('Details')).toBeInTheDocument()
  })

  it('renders plan and full output blocks as tree views', () => {
    const { container } = render(
      <FormattedMessageBody
        body={JSON.stringify({
          plan: { steps: ['one', 'two'] },
          done: false,
        })}
      />,
    )

    expect(screen.getByText('Plan')).toBeInTheDocument()
    expect(screen.getByText('Full output')).toBeInTheDocument()

    const trees = container.querySelectorAll('.message-tree')
    expect(trees).toHaveLength(2)
  })

  describe('markdown rendering in chat panel', () => {
    // Scope:
    //   * Inbound chat bubbles (channel="chat" + payload.chat_response)
    //     get full markdown rendering — including GFM tables.
    //   * Diagnostics channels (agent-outputs / tool-outputs / errors)
    //     stay verbatim. That's the whole point of diagnostics: you
    //     need to see exactly what the LLM produced when debugging.
    //   * Outbound (user-typed) chat messages also stay verbatim —
    //     they take a different parseMessageBody branch.

    it('renders a GFM table from chat_response as a real <table>', () => {
      const tableMd = [
        '| # | Title |',
        '|---|-------|',
        '| 1 | Whitney Houston |',
        '| 2 | Snap! |',
      ].join('\n')
      const { container } = render(
        <FormattedMessageBody
          body="fallback"
          channel="chat"
          payload={{ chat_response: `Top hits:\n\n${tableMd}` }}
        />,
      )

      const table = container.querySelector('table')
      expect(table).not.toBeNull()
      expect(table?.querySelectorAll('tr')).toHaveLength(3)
      expect(screen.getByText('Whitney Houston')).toBeInTheDocument()
      expect(screen.getByText('Snap!')).toBeInTheDocument()
    })

    it('renders bold markdown from chat_response as <strong>', () => {
      const { container } = render(
        <FormattedMessageBody
          body="fallback"
          channel="chat"
          payload={{ chat_response: 'Track **highlight** here.' }}
        />,
      )

      const strong = container.querySelector('strong')
      expect(strong?.textContent).toBe('highlight')
    })

    it('renders markdown inside an <extended_info> body', () => {
      const tableMd = '| A | B |\n|---|---|\n| 1 | 2 |'
      const { container } = render(
        <FormattedMessageBody
          body="fallback"
          channel="chat"
          payload={{
            chat_response: `Here:\n\n<extended_info><summary>Top</summary>${tableMd}</extended_info>`,
          }}
        />,
      )

      expect(screen.getByText('Top')).toBeInTheDocument()
      const table = container.querySelector('.detailed-info-content table')
      expect(table).not.toBeNull()
    })

    it('renders ordered-list markdown inside a <list> body', () => {
      const { container } = render(
        <FormattedMessageBody
          body="fallback"
          channel="chat"
          payload={{
            chat_response: 'Tracks.\n\n<list><summary>Set</summary>1. Track A\n2. Track B</list>',
          }}
        />,
      )

      const ol = container.querySelector('ol')
      expect(ol).not.toBeNull()
      expect(ol?.querySelectorAll('li')).toHaveLength(2)
    })

    it('keeps diagnostics (agent-outputs) verbatim — markdown chars not rendered', () => {
      const { container } = render(
        <FormattedMessageBody
          body="[Response]\n| # | Title |\n|---|-------|\n| 1 | **Whitney** |"
          channel="agent-outputs"
          payload={{ source: '[Response]', text: '| # | Title |\n|---|-------|\n| 1 | **Whitney** |' }}
        />,
      )

      expect(container.querySelector('table')).toBeNull()
      expect(container.querySelector('strong')).toBeNull()
      expect(container.textContent).toContain('**Whitney**')
      expect(container.textContent).toContain('| # | Title |')
    })

    it('keeps outbound user text verbatim — markdown chars not rendered', () => {
      // Outbound messages take the line-163 fallback branch in
      // parseMessageBody (no chat_response payload field). Markdown
      // rendering is gated to the inbound-chat path, so a user typing
      // ``**hello**`` sees their literal characters back.
      const { container } = render(
        <FormattedMessageBody body="**hello** world" channel="chat" />,
      )

      expect(container.querySelector('strong')).toBeNull()
      expect(container.textContent).toContain('**hello**')
    })
  })
})
