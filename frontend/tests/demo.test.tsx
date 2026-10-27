import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHttpApi } from '../src/services/api';
import { createDemoApi } from '../src/services/demo/demoApi';
import { createFakeApi } from './helpers/fakeApi';
import { renderApp } from './helpers/renderApp';

afterEach(() => {
  vi.unstubAllEnvs();
  delete process.env.VITE_DEMO_MODE;
});

describe('demo mode isolation', () => {
  it('refuses to build production with demo mode enabled', async () => {
    process.env.VITE_DEMO_MODE = 'true';
    const { default: config } = await import('../vite.config');
    const factory = config as unknown as (env: { command: string; mode: string }) => unknown;
    expect(() => factory({ command: 'build', mode: 'production' })).toThrow(/not allowed in a production build/);
    expect(() => factory({ command: 'build', mode: 'demo' })).not.toThrow();
    delete process.env.VITE_DEMO_MODE;
    expect(() => factory({ command: 'build', mode: 'production' })).not.toThrow();
  });

  it('labels every screen in demo mode', async () => {
    renderApp('/', createFakeApi('demo'));
    expect(await screen.findByRole('region', { name: 'Demo mode' })).toHaveTextContent('simulated data only');
  });

  it('shows no demo label against the real API', async () => {
    renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    expect(screen.queryByRole('region', { name: 'Demo mode' })).not.toBeInTheDocument();
  });

  it('keeps the live and demo implementations separate', () => {
    expect(createHttpApi({ baseUrl: 'http://api.test', fetchImpl: vi.fn() }).mode).toBe('live');
    expect(createDemoApi().mode).toBe('demo');
  });

  it('does not fall back to demo data when the live API fails', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const api = createHttpApi({ baseUrl: 'http://api.test', fetchImpl, maxReadRetries: 0 });
    await expect(api.applications.list({ page: 1, page_size: 25 })).rejects.toMatchObject({ kind: 'network' });
  });

  it('simulated demo data uses reserved, non-routable names', async () => {
    const api = createDemoApi();
    await api.auth.login({ email: 'a@example.com', password: 'x' });
    const apps = await api.applications.list({ page: 1, page_size: 25 });
    for (const app of apps.items) {
      const full = await api.applications.get(app.id);
      if (full.service_url) expect(new URL(full.service_url).hostname.endsWith('.invalid')).toBe(true);
    }
  }, 15_000);
});
