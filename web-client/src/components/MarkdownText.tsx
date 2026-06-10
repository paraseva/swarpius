import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import s from './MarkdownText.module.css'

interface MarkdownTextProps {
  children: string
}

// Single integration point for rendering markdown in agent chat bubbles.
// GFM is enabled so coordinator-produced tables render as real
// ``<table>`` rather than literal pipes. Diagnostics and
// outbound user messages must NOT use this component — they go to ``<pre>``
// so users can see what the LLM literally produced (diagnostics) or what
// they literally typed (outbound).
export const MarkdownText: React.FC<MarkdownTextProps> = ({ children }) => (
  <div className={s.markdown}>
    <Markdown remarkPlugins={[remarkGfm]}>{children}</Markdown>
  </div>
)
