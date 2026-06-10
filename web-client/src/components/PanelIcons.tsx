import React from 'react'

export const ExpandIcon: React.FC = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path d="M1 6V1h5M10 1h5v5M15 10v5h-5M6 15H1v-5" />
    <path d="M6 1L1 6M10 1l5 5M15 10l-5 5M1 10l5 5" />
  </svg>
)

export const CollapseIcon: React.FC = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path d="M1 6V1h5M10 1h5v5M15 10v5h-5M6 15H1v-5" />
    <path d="M6 6L1 1M10 6l5-5M10 10l5 5M6 10L1 15" />
  </svg>
)
