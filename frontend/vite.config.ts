/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const pkg = JSON.parse(readFileSync(resolve(process.cwd(), 'package.json'), 'utf8')) as { version: string };

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');

  // Demo mode serves simulated data. It must never reach a production bundle,
  // so a production build with it enabled fails instead of shipping it.
  if (command === 'build' && mode === 'production' && env.VITE_DEMO_MODE === 'true') {
    throw new Error(
      'VITE_DEMO_MODE=true is not allowed in a production build. ' +
        'Unset it, or build a non-production preview with `vite build --mode demo`.',
    );
  }

  return {
    plugins: [react()],
    define: { __APP_VERSION__: JSON.stringify(pkg.version) },
    server: { port: 5173 },
    preview: { port: 4173 },
    build: {
      target: 'es2022',
      sourcemap: true,
      // Libraries change less often than app code, so they get their own long-cached chunk.
      rolldownOptions: { output: { codeSplitting: { groups: [{ name: 'vendor', test: /node_modules/ }] } } },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      include: ['tests/**/*.test.{ts,tsx}'],
      restoreMocks: true,
      // Form tests type many fields with user-event; the 5 s default is too tight on a busy machine.
      testTimeout: 30_000,
      env: { VITE_API_BASE_URL: 'http://api.test', VITE_DEMO_MODE: 'false' },
    },
  };
});
