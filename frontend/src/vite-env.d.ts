/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEMO_MODE?: string;
  readonly VITE_SUPPORT_URL?: string;
  readonly VITE_SAMPLE_APP_REPOSITORY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Frontend version from package.json, injected at build time. */
declare const __APP_VERSION__: string;
