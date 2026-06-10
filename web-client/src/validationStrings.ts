import type {
  AgentValidationResult,
  BackendReachabilityResult,
} from './hooks/useSettingsState'

// error_kind → human phrase. An empty phrase means "show the subject
// (agent / backend) name alone" — used for `other` and unknown kinds.
const AGENT_PHRASE: Record<string, string> = {
  auth_failed: 'API key invalid',
  not_found: 'model not found',
  network: 'unreachable',
  bad_request: 'request rejected',
}

const BACKEND_PHRASE: Record<string, string> = {
  auth_failed: 'API key invalid',
  missing_credential: 'API key missing',
  not_found: 'endpoint not found',
  network: 'unreachable',
}

// Sentence subject per backend — the tab label ("Speech") reads oddly in a
// message; key off the backend type for clearer wording.
const BACKEND_SUBJECT: Record<BackendReachabilityResult['backend'], string> = {
  'web-search': 'Web Search',
  'tts': 'TTS server',
}

// Proper casing for providers we know; others fall back to Capitalised.
const PROVIDER_LABEL: Record<string, string> = {
  ANTHROPIC: 'Anthropic',
  OPENAI: 'OpenAI',
  GEMINI: 'Gemini',
  DEEPSEEK: 'DeepSeek',
  MISTRAL: 'Mistral',
  GROQ: 'Groq',
  OPENROUTER: 'OpenRouter',
}

function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1)
}

function subjectWithPhrase(subject: string, phrase: string): string {
  return phrase ? `${subject} ${phrase}` : subject
}

/** "Arbiter model not found" — or just "Arbiter" when the kind is unknown. */
export function formatAgentIssue(r: AgentValidationResult): string {
  const phrase = r.error_kind ? AGENT_PHRASE[r.error_kind] ?? '' : ''
  return subjectWithPhrase(capitalise(r.agent), phrase)
}

export function formatAgentIssues(results: AgentValidationResult[]): string {
  return results.map(formatAgentIssue).join(', ')
}

/** "TTS server unreachable" — or just the subject when the kind is unknown. */
export function formatBackendIssue(b: BackendReachabilityResult): string {
  const subject = BACKEND_SUBJECT[b.backend] ?? b.label
  const phrase = b.error_kind ? BACKEND_PHRASE[b.error_kind] ?? '' : ''
  return subjectWithPhrase(subject, phrase)
}

/**
 * Humanise a required-config env-var name for the "Configuration required"
 * banner: `LLM_MODEL` → "Coordinator model", `LLM_API_KEY_ANTHROPIC` →
 * "Anthropic API key". Unrecognised names pass through unchanged.
 */
export function formatMissingField(field: string): string {
  if (field === 'LLM_MODEL') return 'Coordinator model'
  const keyMatch = /^LLM_API_KEY_(.+)$/.exec(field)
  if (keyMatch) {
    const provider = keyMatch[1]
    const label = PROVIDER_LABEL[provider.toUpperCase()] ?? capitalise(provider.toLowerCase())
    return `${label} API key`
  }
  return field
}
