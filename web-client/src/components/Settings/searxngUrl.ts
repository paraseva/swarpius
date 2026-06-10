export type SearxngScheme = 'http://' | 'https://'

export interface ParsedSearxngUrl {
  /** null when the input has no recognised scheme — the caller keeps the
   *  current dropdown selection rather than resetting it. */
  scheme: SearxngScheme | null
  /** Everything after the scheme: host[:port] and any path. */
  rest: string
}

const SCHEME_RE = /^(https?):\/\//i

/** Split a stored SEARXNG_URL into the protocol dropdown value + the
 *  host/path remainder. */
export function parseSearxngUrl(url: string): ParsedSearxngUrl {
  const trimmed = url.trim()
  const m = SCHEME_RE.exec(trimmed)
  if (!m) return { scheme: null, rest: trimmed }
  return {
    scheme: `${m[1].toLowerCase()}://` as SearxngScheme,
    rest: trimmed.slice(m[0].length),
  }
}

/** Recombine the dropdown + host field into a stored SEARXNG_URL;
 *  empty host means "unset". */
export function combineSearxngUrl(scheme: SearxngScheme, rest: string): string {
  const host = rest.trim()
  return host ? `${scheme}${host}` : ''
}
