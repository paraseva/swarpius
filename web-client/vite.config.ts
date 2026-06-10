import { readFileSync } from 'node:fs'
import { defineConfig } from 'vitest/config'
import type { Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// Preload the bundled latin / latin-ext font faces so they don't swap in on
// first paint. Build-only (matches the hashed output bundle); rarer scripts
// keep loading on demand via Fontsource.
function preloadLatinFonts(): Plugin {
  return {
    name: 'preload-latin-fonts',
    transformIndexHtml(html, ctx) {
      if (!ctx.bundle) return // build-only; dev serves fonts directly
      const tags = Object.keys(ctx.bundle)
        .filter((file) => /-latin(-ext)?-.*\.woff2$/.test(file))
        .map((file) => ({
          tag: 'link',
          injectTo: 'head-prepend' as const,
          attrs: { rel: 'preload', as: 'font', type: 'font/woff2', href: `/${file}`, crossorigin: '' },
        }))
      return { html, tags }
    },
  }
}

// App version from this package's package.json — the npm mirror of
// agent/VERSION, kept equal by agent/tests/test_version_sync.py + the
// installer build. Injected as __APP_VERSION__ (shown in Settings, compared
// by the update check).
const appVersion = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
).version

// https://vite.dev/config/
export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    react({
      babel: {
        plugins: [['babel-plugin-react-compiler']],
      },
    }),
    preloadLatinFonts(),
  ],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setupTests.ts',
  },
  server: {
    host: '0.0.0.0',
  },
  preview: {
    host: '0.0.0.0',
  },
  build: {
    rollupOptions: {
      output: {
        // Split vendor deps from app code so React and markdown
        // libraries (which change rarely) end up in long-cacheable
        // chunks separate from the app bundle that changes every
        // commit. ``react`` is its own chunk because it's the
        // single biggest dep and the most stable.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          const match = id.match(/node_modules\/(?:\.pnpm\/)?(@[^/]+\/[^/]+|[^/]+)/)
          if (!match) return
          const pkg = match[1]
          if (pkg === 'react' || pkg === 'react-dom' || pkg === 'scheduler') return 'react'
          return 'vendor'
        },
      },
    },
  },
})
