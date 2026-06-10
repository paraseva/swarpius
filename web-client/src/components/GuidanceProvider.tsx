import React from 'react'
import { parseGuidanceSections } from '../utils/parseGuidanceSections'
import { GuidanceContext } from './guidanceContext'
import guideMd from '../assets/guide.md?raw'

const sections = {
  ...parseGuidanceSections(guideMd, 'guide'),
}

export const GuidanceProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <GuidanceContext value={sections}>{children}</GuidanceContext>
)
