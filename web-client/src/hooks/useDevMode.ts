import React from 'react'

const STORAGE_KEY = 'swarpius:dev-mode'

export const useDevMode = () => {
  const [isDevMode, setIsDevMode] = React.useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  const toggleDevMode = React.useCallback(() => {
    setIsDevMode((prev) => {
      const next = !prev
      try {
        localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
      } catch { /* ignore */ }
      return next
    })
  }, [])

  return { isDevMode, toggleDevMode }
}
