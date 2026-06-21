import { createContext, useContext } from 'react'

/** A request the user has focused by clicking its request-id badge. Every open
 *  request-aware surface (except the one clicked) scrolls to it. ``nonce`` makes
 *  re-clicking the same request re-fire the sync. */
export interface FocusedRequest {
  requestId: string
  /** The surface the click came from — it stays put rather than scrolling. */
  sourceKey: string
  nonce: number
}

export interface RequestFocusContextValue {
  focusedRequest: FocusedRequest | null
  focusRequest: (requestId: string, sourceKey: string) => void
}

export const RequestFocusContext = createContext<RequestFocusContextValue | undefined>(undefined)

/** Optional — returns undefined outside a provider (e.g. analysis views), so a
 *  badge there is copy-only. */
export const useRequestFocus = (): RequestFocusContextValue | undefined =>
  useContext(RequestFocusContext)
