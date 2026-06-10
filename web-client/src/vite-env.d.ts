/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** App version, injected at build from package.json (see vite.config.ts). */
declare const __APP_VERSION__: string
