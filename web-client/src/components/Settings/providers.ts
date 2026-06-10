/**
 * Provider metadata for the Settings tabs.
 *
 * Each entry describes one LLM provider: how it's referred to in the
 * model string, its display name, the env var that holds its API key
 * (if any), and whether the Settings UI can auto-test it for free.
 *
 * Single source of truth so the Essentials tab's provider radio, the
 * API Keys tab's per-provider section, and the test-connection
 * dispatch all agree.
 */

export interface LlmProviderInfo {
  /** Provider prefix used in LLM_MODEL (`<id>/<model-name>`). */
  id: string
  label: string
  /** Env var name for the API key, or null for keyless providers. */
  apiKeyEnv: string | null
  /** True for providers that run locally (Ollama). */
  local?: boolean
  /** True if the agent can run a free auth check at startup. */
  freeAutoCheck?: boolean
  /** True iff we've end-to-end tested this provider. Untested ones
   * are listed under the "Other LiteLLM providers" group. */
  verified: boolean
  /** Example model identifier shown as the input placeholder. */
  modelPlaceholder: string
  /** Link to where users get a key (URL, no sign-up wall). */
  helpUrl?: string
}

// Verified providers (tested end-to-end) are in the "Verified"
// group; popular LiteLLM-supported but untested ones in the "Other"
// group. Long-tail providers (Cohere, Bedrock, Replicate, …) aren't
// listed here — the agent's generic LLM_API_KEY_<PROVIDER> lookup
// means they work via direct .env editing.
//
// "gemini" matches LiteLLM's canonical id for Google AI Studio.
// "ollama_chat" matches the agent's local-provider whitelist.
export const LLM_PROVIDERS: LlmProviderInfo[] = [
  // ── Verified ─────────────────────────────────────────────────
  {
    id: 'anthropic',
    label: 'Anthropic',
    apiKeyEnv: 'LLM_API_KEY_ANTHROPIC',
    verified: true,
    freeAutoCheck: true,
    modelPlaceholder: 'claude-sonnet-4-6',
    helpUrl: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    apiKeyEnv: 'LLM_API_KEY_OPENAI',
    verified: true,
    freeAutoCheck: true,
    modelPlaceholder: 'gpt-5.4',
    helpUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'gemini',
    label: 'Google Gemini',
    apiKeyEnv: 'LLM_API_KEY_GEMINI',
    verified: true,
    freeAutoCheck: true,
    modelPlaceholder: 'gemini-2.5-pro',
    helpUrl: 'https://aistudio.google.com/app/apikey',
  },
  {
    id: 'ollama_chat',
    label: 'Ollama (local)',
    apiKeyEnv: null,
    local: true,
    verified: true,
    freeAutoCheck: true,
    modelPlaceholder: 'gemma4:26b',
  },
  // ── Other LiteLLM providers (untested) ────────────────────────
  {
    id: 'openrouter',
    label: 'OpenRouter',
    apiKeyEnv: 'LLM_API_KEY_OPENROUTER',
    verified: false,
    modelPlaceholder: 'meta-llama/llama-3.1-70b-instruct',
    helpUrl: 'https://openrouter.ai/keys',
  },
  {
    id: 'groq',
    label: 'Groq',
    apiKeyEnv: 'LLM_API_KEY_GROQ',
    verified: false,
    modelPlaceholder: 'llama-3.1-70b-versatile',
    helpUrl: 'https://console.groq.com/keys',
  },
  {
    id: 'mistral',
    label: 'Mistral',
    apiKeyEnv: 'LLM_API_KEY_MISTRAL',
    verified: false,
    modelPlaceholder: 'mistral-large-latest',
    helpUrl: 'https://console.mistral.ai/api-keys',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    apiKeyEnv: 'LLM_API_KEY_DEEPSEEK',
    verified: false,
    modelPlaceholder: 'deepseek-chat',
    helpUrl: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'together_ai',
    label: 'Together AI',
    apiKeyEnv: 'LLM_API_KEY_TOGETHER_AI',
    verified: false,
    modelPlaceholder: 'meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo',
    helpUrl: 'https://api.together.xyz/settings/api-keys',
  },
  {
    id: 'perplexity',
    label: 'Perplexity',
    apiKeyEnv: 'LLM_API_KEY_PERPLEXITY',
    verified: false,
    modelPlaceholder: 'sonar-pro',
    helpUrl: 'https://www.perplexity.ai/settings/api',
  },
  {
    id: 'xai',
    label: 'xAI (Grok)',
    apiKeyEnv: 'LLM_API_KEY_XAI',
    verified: false,
    modelPlaceholder: 'grok-2-latest',
    helpUrl: 'https://console.x.ai',
  },
]

/** True iff at least one provider in the list is unverified — used to
 * decide whether to render the "untested" group label at all. */
export function hasUnverifiedProviders(): boolean {
  return LLM_PROVIDERS.some((p) => !p.verified)
}

/** Lookup helper. Falls back to a synthetic record for unknown
 *  provider strings the user has typed manually, so the UI still
 *  renders something sensible. */
export function getProvider(id: string): LlmProviderInfo {
  const found = LLM_PROVIDERS.find((p) => p.id === id.toLowerCase())
  if (found) return found
  return {
    id: id || 'unknown',
    label: id || 'Unknown',
    apiKeyEnv: null,
    verified: false,
    modelPlaceholder: 'model-name',
  }
}

/** Parse a model string of the form "provider/model-name" into parts.
 *  Returns nulls for anything that doesn't match. */
export function parseModelString(
  model: string | null | undefined,
): { provider: string | null; model: string | null } {
  if (!model) return { provider: null, model: null }
  const idx = model.indexOf('/')
  if (idx <= 0) return { provider: null, model: null }
  return {
    provider: model.slice(0, idx),
    model: model.slice(idx + 1),
  }
}
