import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createHttpApi } from '../src/services/api';
import { decode } from '../src/services/api/decode';
import { deployment } from '../src/services/api/decoders';
import { createDemoApi } from '../src/services/demo/demoApi';
import { capabilityFindings, hasBlockingFindings, type SpecToCheck } from '../src/utils/capabilities';
import { deploymentTrend } from '../src/utils/deploymentTrend';
import { validateRows } from '../src/utils/environment';
import { secretReferenceProvider } from '../src/utils/validation';
import { createFakeApi, pending } from './helpers/fakeApi';
import { makeAppSummary, makeCapabilities, makeDeployment, makeSession, page } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('application shell', () => {
  it('keeps the sidebar highlight in sync with the route, including nested settings pages', async () => {
    renderApp('/settings/platform');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current');
  });

  it('collapses the desktop sidebar to icons while keeping accessible link names', async () => {
    const { user } = renderApp('/');
    await user.click(await screen.findByRole('button', { name: 'Collapse sidebar' }));
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Deployments' })).toHaveAttribute('title', 'Deployments');
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens command search with Ctrl+K and navigates to an application found through the API', async () => {
    const api = createFakeApi();
    const { user, router } = renderApp('/', api);
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: 'Search CloudBridge' });
    await user.type(within(dialog).getByRole('combobox'), 'orders');
    // Wait for the search on the full text first: on a slow machine an earlier partial search can
    // render results that are replaced as soon as the final search starts.
    await waitFor(() => expect(api.applications.list).toHaveBeenCalledWith(expect.objectContaining({ search: 'orders' }), expect.anything()));
    const option = await within(dialog).findByRole('option', { name: /orders-api/ });
    await user.click(option);
    await waitFor(() => expect(router.state.location.pathname).toBe('/applications/app-1'));
  });

  it('keeps application results visible while the next search loads', async () => {
    const api = createFakeApi();
    renderApp('/', api);
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: 'Search CloudBridge' });
    const box = within(dialog).getByRole('combobox');
    fireEvent.change(box, { target: { value: 'ord' } });
    await within(dialog).findByRole('option', { name: /orders-api/ });
    api.applications.list.mockReturnValue(pending()); // the next search never answers
    fireEvent.change(box, { target: { value: 'orde' } });
    await waitFor(() => expect(api.applications.list).toHaveBeenCalledWith(expect.objectContaining({ search: 'orde' }), expect.anything()));
    expect(within(dialog).getByRole('option', { name: /orders-api/ })).toBeInTheDocument();
  });

  it('lists real failures in the notifications menu', async () => {
    const api = createFakeApi();
    api.deployments.list.mockImplementation(async (params) =>
      params.status?.includes('failed') ? page([makeDeployment({ status: 'failed', application_name: 'billing-api' })]) : page([]),
    );
    const { user } = renderApp('/', api);
    await user.click(await screen.findByRole('button', { name: 'Notifications' }));
    const panel = await screen.findByRole('region', { name: 'Needs attention' });
    expect(await within(panel).findByText('billing-api')).toBeInTheDocument();
    expect(within(panel).getByText('Failed')).toBeInTheDocument();
  });

  it('shows the user avatar and an account menu', async () => {
    const { user } = renderApp('/');
    await user.click(await screen.findByRole('button', { name: 'Account menu for Ada Admin' }));
    expect(screen.getByRole('menuitem', { name: /Sign out/ })).toBeInTheDocument();
    expect(screen.getByText('AA')).toBeInTheDocument();
  });
});

describe('applications list filtering and sorting', () => {
  it('filters by health and provider, and sorts through the URL', async () => {
    const api = createFakeApi();
    const { user, router } = renderApp('/applications', api);
    await screen.findByRole('table', { name: 'Applications' });
    await user.selectOptions(screen.getByLabelText('Health'), 'unhealthy,degraded');
    await user.selectOptions(screen.getByLabelText('Provider'), 'aws_app_runner');
    await user.click(screen.getByRole('button', { name: /^Updated/ }));
    await waitFor(() =>
      expect(api.applications.list).toHaveBeenLastCalledWith(
        expect.objectContaining({
          health: ['unhealthy', 'degraded'],
          provider: 'aws_app_runner',
          sort: { key: 'updated_at', direction: 'asc' },
        }),
        expect.anything(),
      ),
    );
    expect(router.state.location.search).toContain('sort=updated_at');
    await user.click(screen.getByRole('button', { name: /^Updated/ }));
    await waitFor(() => expect(router.state.location.search).toContain('sort=-updated_at'));
  });

  it('shows type, runtime, target, health in text and the serving version', async () => {
    const api = createFakeApi();
    api.applications.list.mockResolvedValue(page([makeAppSummary({ health: 'degraded', current_version: '3.1.4' })]));
    renderApp('/applications', api);
    const table = await screen.findByRole('table', { name: 'Applications' });
    expect(within(table).getByText('API')).toBeInTheDocument();
    expect(within(table).getByText('Node.js')).toBeInTheDocument();
    expect(within(table).getByText('AWS App Runner')).toBeInTheDocument();
    expect(within(table).getByText('Degraded')).toBeInTheDocument();
    expect(within(table).getByText('3.1.4')).toBeInTheDocument();
  });
});

describe('API service layer', () => {
  function capture() {
    const calls: Array<{ url: string; method: string }> = [];
    const fetchImpl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? 'GET' });
      return new Response('{}', { status: 500, headers: { 'content-type': 'application/json' } });
    });
    const api = createHttpApi({ baseUrl: 'http://api.test', fetchImpl: fetchImpl as unknown as typeof fetch, maxReadRetries: 0 });
    return { api, calls };
  }

  it('uses the documented backend paths', async () => {
    const { api, calls } = capture();
    const write = { idempotencyKey: 'k' };
    await Promise.allSettled([
      api.applications.list({ page: 1, page_size: 25 }),
      api.applications.get('a1'),
      api.applications.create({} as never, write),
      api.applications.updateConfiguration('a1', {} as never),
      api.applications.validate('a1'),
      api.providers.getCapabilities('gcp_cloud_run'),
      api.providers.list(),
      api.providers.create({} as never, write),
      api.providers.validate('c1'),
      api.deployments.listForApplication('a1', { page: 1, page_size: 10 }),
      api.deployments.create('a1', write),
      api.deployments.get('d1'),
      api.deployments.listEvents('d1'),
      api.deployments.getLogs('d1'),
      api.deployments.rollback('d1', write),
    ]);
    const seen = calls.map((c) => `${c.method} ${c.url.replace('http://api.test', '').split('?')[0]}`);
    expect(seen).toEqual([
      'GET /api/v1/apps',
      'GET /api/v1/apps/a1',
      'POST /api/v1/apps',
      'PUT /api/v1/apps/a1/spec',
      'POST /api/v1/apps/a1/validate',
      'GET /api/v1/providers/gcp_cloud_run/capabilities',
      'GET /api/v1/cloud-connections',
      'POST /api/v1/cloud-connections',
      'POST /api/v1/cloud-connections/c1/test',
      'GET /api/v1/apps/a1/deployments',
      'POST /api/v1/apps/a1/deployments',
      'GET /api/v1/deployments/d1',
      'GET /api/v1/deployments/d1/events',
      'GET /api/v1/deployments/d1/logs',
      'POST /api/v1/deployments/d1/rollback',
    ]);
  });

  it('serialises multi-value filters and sort order', async () => {
    const { api, calls } = capture();
    await api.deployments
      .list({ page: 2, page_size: 10, status: ['failed', 'failed_rollback'], sort: { key: 'created_at', direction: 'desc' } })
      .catch(() => undefined);
    const url = new URL(calls[0]!.url);
    expect(url.searchParams.get('status')).toBe('failed,failed_rollback');
    expect(url.searchParams.get('sort')).toBe('-created_at');
  });
});

describe('decoding deployments', () => {
  it('accepts upper-case states and never grants actions the backend did not send', () => {
    const raw: Record<string, unknown> = { ...makeDeployment(), status: 'FAILED_ROLLBACK' };
    delete raw.allowed_actions;
    const d = decode(deployment, raw);
    expect(d.status).toBe('failed_rollback');
    expect(d.allowed_actions).toEqual({ rollback: false, cancel: false, approve: false });
  });
});

describe('provider capability rules', () => {
  const spec: SpecToCheck = {
    application_type: 'web_service',
    region: 'eu-west-1',
    cpu: '1',
    memory: '2Gi',
    min_instances: 1,
    max_instances: 3,
    concurrency: null,
    exposure: 'public',
    secretRefCount: 0,
  };

  it('finds nothing to report for a supported configuration', () => {
    expect(capabilityFindings(spec, makeCapabilities())).toEqual([]);
  });

  it('separates blocking problems from adjustments and ignored settings', () => {
    const findings = capabilityFindings(
      { ...spec, min_instances: 0, max_instances: 40, concurrency: 50, region: 'mars-1' },
      makeCapabilities({ supports_concurrency: false }),
    );
    expect(findings.map((f) => [f.field, f.severity])).toEqual([
      ['region', 'error'],
      ['min_instances', 'adjusted'],
      ['max_instances', 'error'],
      ['concurrency', 'ignored'],
    ]);
    expect(hasBlockingFindings(findings)).toBe(true);
    expect(hasBlockingFindings(findings.filter((f) => f.severity !== 'error'))).toBe(false);
  });

  it('flags private exposure and secret references the provider cannot honour', () => {
    const findings = capabilityFindings(
      { ...spec, exposure: 'private', secretRefCount: 2 },
      makeCapabilities({ supports_private_exposure: false, supports_secret_references: false }),
    );
    expect(findings.map((f) => f.field)).toEqual(['exposure', 'secret_refs']);
  });
});

describe('environment rows', () => {
  it('rejects duplicate names across variables and secrets, and invalid references', () => {
    const errors = validateRows(
      [{ key: '1', name: 'DATABASE_URL', value: 'x' }],
      [
        { key: '2', name: 'DATABASE_URL', reference: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:db' },
        { key: '3', name: 'TOKEN', reference: 'plain-text-token' },
      ],
    );
    expect(errors['secret_refs.0.name']).toMatch(/already used/);
    expect(errors['secret_refs.1.reference']).toMatch(/Secrets Manager/);
  });

  it('recognises which provider a secret reference belongs to', () => {
    expect(secretReferenceProvider('arn:aws:secretsmanager:eu-west-1:123456789012:secret:app/db')).toBe('aws_app_runner');
    expect(secretReferenceProvider('projects/acme/secrets/db/versions/latest')).toBe('gcp_cloud_run');
    expect(secretReferenceProvider('https://acme-vault.vault.azure.net/secrets/db-password')).toBe('azure_container_apps');
    expect(secretReferenceProvider('https://evil.example.com/secrets/db-password')).toBeNull();
    expect(secretReferenceProvider('db-password')).toBeNull();
  });
});

describe('deployment trend', () => {
  it('counts only finished deployments per local day and never invents data', () => {
    const now = new Date(2026, 9, 14, 12, 0, 0);
    const at = (daysAgo: number) => new Date(2026, 9, 14 - daysAgo, 9, 0, 0).toISOString();
    const days = deploymentTrend(
      [
        makeDeployment({ status: 'succeeded', finished_at: at(0) }),
        makeDeployment({ status: 'failed', finished_at: at(0) }),
        makeDeployment({ status: 'rolled_back', kind: 'deploy', finished_at: at(1) }),
        makeDeployment({ status: 'rolled_back', kind: 'rollback', finished_at: at(1) }),
        makeDeployment({ status: 'building', finished_at: null }),
        makeDeployment({ status: 'succeeded', finished_at: at(30) }),
      ],
      14,
      now,
    );
    expect(days).toHaveLength(14);
    expect(days[13]).toMatchObject({ succeeded: 1, failed: 1 });
    expect(days[12]).toMatchObject({ succeeded: 1, failed: 1 });
    expect(days.slice(0, 12).every((d) => d.succeeded === 0 && d.failed === 0)).toBe(true);
  });

  it('shows the chart with a legend and a table view on the overview', async () => {
    const api = createFakeApi();
    api.deployments.list.mockResolvedValue(page([makeDeployment({ status: 'succeeded', finished_at: new Date().toISOString() })]));
    renderApp('/', api);
    expect(await screen.findByRole('img', { name: /1 succeeded and 0 failed deployments/ })).toBeInTheDocument();
    expect(screen.getByText('View as table')).toBeInTheDocument();
  });
});

describe('demo mode behaviour', () => {
  it('only permits rolling back to an earlier revision, not the one serving traffic', async () => {
    const api = createDemoApi();
    await api.auth.login({ email: 'a@example.com', password: 'x' });
    const serving = await api.deployments.get('demo-dep-3');
    const earlier = await api.deployments.get('demo-dep-1');
    expect(serving.allowed_actions.rollback).toBe(false);
    expect(earlier.allowed_actions.rollback).toBe(true);
    await expect(api.deployments.rollback('demo-dep-3', { idempotencyKey: 'k1' })).rejects.toMatchObject({ status: 409 });
  });

  it('reports simulated capabilities and labels simulated logs', async () => {
    const api = createDemoApi();
    await api.auth.login({ email: 'a@example.com', password: 'x' });
    expect((await api.providers.getCapabilities('aws_app_runner')).min_instances_floor).toBe(1);
    const logs = await api.deployments.getLogs('demo-dep-3');
    expect(logs.entries.length).toBeGreaterThan(0);
    expect(logs.entries.every((e) => e.message.startsWith('[demo]'))).toBe(true);
  });

  it('rejects creating an application the provider cannot run', async () => {
    const api = createDemoApi();
    await api.auth.login({ email: 'a@example.com', password: 'x' });
    await expect(
      api.applications.create(
        {
          name: 'queue-worker',
          description: null,
          application_type: 'worker',
          runtime: 'go',
          connection_id: 'demo-conn-aws',
          region: 'eu-west-1',
          source: { type: 'image', image: 'r.example.com/w:1', repository_url: null, ref_type: null, git_ref: null, dockerfile_path: null, build_context: null },
          port: 8080,
          cpu: '1',
          memory: '2Gi',
          min_instances: 1,
          max_instances: 2,
          concurrency: null,
          exposure: 'public',
          health_check_path: null,
          environment: [],
          secret_refs: [],
        },
        { idempotencyKey: 'k2' },
      ),
    ).rejects.toMatchObject({ status: 422 });
  });
});

describe('activity log filters', () => {
  it('filters by actor and action, separately from runtime logs', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/activity', api);
    await screen.findByRole('table', { name: 'Activity log' });
    await user.type(screen.getByLabelText('Actor'), 'ada');
    await user.selectOptions(screen.getByLabelText('Action'), 'deployment.rollback_started');
    await waitFor(() =>
      expect(api.audit.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ actor: 'ada', action: 'deployment.rollback_started' }),
        expect.anything(),
      ),
    );
    expect(screen.getByText(/application output is under each application's Logs tab/)).toBeInTheDocument();
  });
});

describe('settings', () => {
  it('shows the current session and clearly disables unsupported session revocation', async () => {
    renderApp('/settings/sessions');
    expect(await screen.findByText('ada@example.com', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign out' })).toHaveAttribute('href', '/logout');
    const revoke = screen.getByRole('button', { name: 'Sign out of all other sessions' });
    expect(revoke).toBeDisabled();
    expect(revoke).toHaveAccessibleDescription(/no endpoint/);
  });

  it('shows platform information without exposing anything secret', async () => {
    renderApp('/settings/platform');
    expect(await screen.findByText('CloudBridge API (live)')).toBeInTheDocument();
    expect(screen.getByText('/api/v1')).toBeInTheDocument();
    expect(screen.getByText('Not yet supported')).toBeInTheDocument();
  });

  it('lists unsupported settings sections and explains them instead of hiding them', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession(undefined, { features: { organization_settings: false, notification_preferences: false } }));
    renderApp('/settings/organization', api);
    expect(await screen.findByRole('heading', { name: 'Organization settings are not available' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Settings sections' })).toHaveTextContent('Notifications');
  });
});

describe('cloud providers page', () => {
  it('shows a panel per provider with capabilities, permissions and validation result', async () => {
    renderApp('/providers');
    const aws = await screen.findByRole('region', { name: 'AWS App Runner' });
    const gcp = screen.getByRole('region', { name: 'Google Cloud Run' });
    expect(within(aws).getByRole('link', { name: 'Production AWS' })).toBeInTheDocument();
    expect(within(aws).getByText('123456789012')).toBeInTheDocument();
    expect(await within(aws).findByText('Up to 200 requests per instance')).toBeInTheDocument();
    expect(within(aws).getByText('Required permissions')).toBeInTheDocument();
    expect(within(gcp).getByRole('heading', { name: 'No Google Cloud Run connection' })).toBeInTheDocument();
  });

  it('starts validation from the panel and reports progress, not success', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/providers', api);
    const aws = await screen.findByRole('region', { name: 'AWS App Runner' });
    await user.click(within(aws).getByRole('button', { name: 'Validate' }));
    await waitFor(() => expect(api.providers.validate).toHaveBeenCalledWith('conn-1'));
    expect(await screen.findByText('Validation started')).toBeInTheDocument();
    expect(screen.queryByText('Provider connected')).not.toBeInTheDocument();
  });
});
