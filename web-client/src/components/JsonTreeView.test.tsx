import { render, fireEvent, within, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { JsonTreeView } from './JsonTreeView'

afterEach(cleanup)

describe('JsonTreeView', () => {
  it('renders null', () => {
    const { container } = render(<JsonTreeView data={null} />)
    expect(container.querySelector('span')).toHaveTextContent('null')
  })

  it('renders booleans', () => {
    const { container } = render(<JsonTreeView data={true} />)
    expect(container.querySelector('span')).toHaveTextContent('true')
  })

  it('renders numbers', () => {
    const { container } = render(<JsonTreeView data={42} />)
    expect(container.querySelector('span')).toHaveTextContent('42')
  })

  it('renders simple strings with quotes', () => {
    const { container } = render(<JsonTreeView data="hello" />)
    expect(container.textContent).toContain('"hello"')
  })

  it('renders multi-line strings in a pre block', () => {
    const { container } = render(<JsonTreeView data={'line1\nline2\nline3'} />)
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toBe('line1\nline2\nline3')
  })

  it('renders empty object as {}', () => {
    const { container } = render(<JsonTreeView data={{}} />)
    expect(container.textContent).toContain('{}')
  })

  it('renders object keys expanded at depth 0', () => {
    const { container } = render(<JsonTreeView data={{ name: 'test', count: 5 }} />)
    const view = within(container)
    expect(view.getByText('name')).toBeInTheDocument()
    expect(view.getByText('count')).toBeInTheDocument()
  })

  it('renders empty array as []', () => {
    const { container } = render(<JsonTreeView data={[]} />)
    expect(container.textContent).toContain('[]')
  })

  it('renders array items expanded at depth 0', () => {
    const { container } = render(<JsonTreeView data={['a', 'b']} />)
    const view = within(container)
    expect(view.getByText('0')).toBeInTheDocument()
    expect(view.getByText('1')).toBeInTheDocument()
  })

  it('collapses nested objects at depth >= 2', () => {
    const { container } = render(<JsonTreeView data={{ a: { b: { c: 'deep' } } }} />)
    const view = within(container)
    expect(view.getByText('a')).toBeInTheDocument()
    expect(view.getByText('b')).toBeInTheDocument()
    expect(view.getByText('{1}')).toBeInTheDocument()
    expect(view.queryByText('c')).not.toBeInTheDocument()
  })

  it('expands collapsed node on toggle click', () => {
    const { container } = render(<JsonTreeView data={{ a: { b: { c: 'deep' } } }} />)
    const view = within(container)
    expect(view.queryByText('c')).not.toBeInTheDocument()

    fireEvent.click(view.getByText('{1}'))

    expect(view.getByText('c')).toBeInTheDocument()
  })

  it('collapses expanded node on toggle click', () => {
    const { container } = render(<JsonTreeView data={{ name: 'test' }} />)
    const view = within(container)
    expect(view.getByText('name')).toBeInTheDocument()

    const toggle = view.getByRole('button', { expanded: true })
    fireEvent.click(toggle)

    expect(view.queryByText('name')).not.toBeInTheDocument()
    expect(view.getByText('{1}')).toBeInTheDocument()
  })

  it('parses embedded JSON strings', () => {
    const { container } = render(<JsonTreeView data={'{"key":"value"}'} />)
    const view = within(container)
    expect(view.getByText('JSON')).toBeInTheDocument()
    expect(view.getByText('key')).toBeInTheDocument()
  })

  it('does not parse non-JSON strings starting with {', () => {
    const { container } = render(<JsonTreeView data="{not json" />)
    const view = within(container)
    expect(view.queryByText('JSON')).not.toBeInTheDocument()
    expect(view.getByText('"{not json"')).toBeInTheDocument()
  })

  it('applies className to container', () => {
    const { container } = render(<JsonTreeView data={null} className="custom" />)
    expect(container.firstChild).toHaveClass('custom')
  })

  it('renders nested structures correctly', () => {
    const { container } = render(
      <JsonTreeView
        data={{
          tool: 'roon_search',
          input: { query: 'jazz', zone: 'Living Room' },
          results: [{ title: 'Album A' }, { title: 'Album B' }],
        }}
      />,
    )
    const view = within(container)

    expect(view.getByText('tool')).toBeInTheDocument()
    expect(view.getByText('input')).toBeInTheDocument()
    expect(view.getByText('results')).toBeInTheDocument()
    expect(view.getByText('query')).toBeInTheDocument()
    expect(view.getByText('zone')).toBeInTheDocument()
  })
})
