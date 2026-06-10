import React from 'react'

export interface GettingStartedControls {
  /** Open the Getting Started intro on demand (e.g. the Settings
   *  header button), independent of the first-run auto-open. */
  open: () => void
}

export const GettingStartedContext = React.createContext<GettingStartedControls>({
  open: () => {},
})

export const useGettingStarted = () => React.useContext(GettingStartedContext)
