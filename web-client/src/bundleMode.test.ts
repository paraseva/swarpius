import { afterEach, describe, expect, it } from 'vitest'
import { rememberBundleMode, wasBundleMode } from './bundleMode'

afterEach(() => {
  window.localStorage.clear()
})

describe('bundle-mode persistence', () => {
  it('is false before anything is remembered', () => {
    expect(wasBundleMode()).toBe(false)
  })

  it('remembers a bundle session across a cold load', () => {
    rememberBundleMode(true)
    expect(wasBundleMode()).toBe(true)
  })

  it('clears the flag for a non-bundle session (self-corrects across run modes)', () => {
    rememberBundleMode(true)
    rememberBundleMode(false)
    expect(wasBundleMode()).toBe(false)
  })
})
