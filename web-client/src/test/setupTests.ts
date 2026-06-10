import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// React Testing Library only auto-registers cleanup when `globals: true`
// is set in vitest config. We use explicit imports instead, so wire
// cleanup up here — otherwise DOM from one test leaks into the next
// and `screen.get*` queries can match elements from prior renders.
afterEach(() => {
  cleanup()
})

// JSDOM includes no ResizeObserver; useStickyBottomScroll relies on it.
// Tests that need to trigger the callback install their own fake;
// here we just provide a non-functional default so unrelated tests don't
// crash when their component tree happens to mount the hook.
if (!('ResizeObserver' in globalThis)) {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
}
