import React from 'react'
import type { GuidanceEntry } from '../utils/parseGuidanceSections'

export const GuidanceContext = React.createContext<Record<string, GuidanceEntry>>({})

export const useGuidance = () => React.useContext(GuidanceContext)
