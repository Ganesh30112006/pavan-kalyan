import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// jsdom has no matchMedia; components read prefers-reduced-motion through it.
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

// jsdom logs "not implemented" for scrollTo; the shell calls it after navigation.
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;

// The default 1s wait for findBy/waitFor is too tight on a busy machine (CI, parallel test runs, or the
// backend suite running at the same time). Passing waits return as soon as they succeed, so a long limit
// costs nothing.
configure({ asyncUtilTimeout: 10_000 });

afterEach(() => cleanup());
