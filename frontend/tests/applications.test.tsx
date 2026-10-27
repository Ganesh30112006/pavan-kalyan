import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../src/services/api/errors';
import { createFakeApi, httpError, pending } from './helpers/fakeApi';
import { makeAppSummary, makeConfig, makeConnection, makeSession, page } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('applications list', () => {
  it('shows a table-shaped skeleton while loading', async () => {
    const api = createFakeApi();
    api.applications.list.mockReturnValue(pending());
    renderApp('/applications', api);
    expect(await screen.findByText('Loading applications…')).toBeInTheDocument();
  });

  it('lists applications', async () => {
    renderApp('/applications');
    const table = await screen.findByRole('table', { name: 'Applications' });
    expect(within(table).getByRole('link', { name: 'orders-api' })).toHaveAttribute('href', '/applications/app-1');
    expect(within(table).getByText('Running')).toBeInTheDocument();
  });

  it('explains an empty organization and offers to create an application', async () => {
    const api = createFakeApi();
    api.applications.list.mockResolvedValue(page([]));
    renderApp('/applications', api);
    expect(await screen.findByRole('heading', { name: 'No applications yet' })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Create application' }).length).toBeGreaterThan(0);
  });

  it('distinguishes "no search results" and lets the user clear the search', async () => {
    const api = createFakeApi();
    api.applications.list.mockImplementation(async (params) => (params.search ? page([]) : page([makeAppSummary()])));
    const { user } = renderApp('/applications?q=zzz', api);
    expect(await screen.findByRole('heading', { name: 'No matching applications' })).toBeInTheDocument();
    expect(screen.getByText(/Nothing matched “zzz”/)).toBeInTheDocument();
    const before = api.applications.list.mock.calls.length;
    await user.click(screen.getByRole('button', { name: 'Clear search and filters' }));
    expect(await screen.findByRole('link', { name: 'orders-api' }, { timeout: 3000 })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 400)); // past the search debounce
    // Clearing must not briefly re-apply the old search.
    const searches = api.applications.list.mock.calls.slice(before).map(([params]) => params.search);
    expect(searches).not.toContain('zzz');
  });

  it('shows an error with retry and the request id, then recovers', async () => {
    const api = createFakeApi();
    api.applications.list.mockRejectedValueOnce(httpError(500, { requestId: 'req-500' })).mockResolvedValue(page([makeAppSummary()]));
    const { user } = renderApp('/applications', api);
    expect(await screen.findByRole('heading', { name: 'Something went wrong on our side' })).toBeInTheDocument();
    expect(screen.getByText('req-500')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'orders-api' })).toBeInTheDocument();
  });

  it('keeps the last data visible when a refresh fails', async () => {
    const api = createFakeApi();
    api.applications.list.mockResolvedValueOnce(page([makeAppSummary()])).mockRejectedValue(new ApiError({ kind: 'network' }));
    const { user } = renderApp('/applications', api);
    await screen.findByRole('link', { name: 'orders-api' });
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Could not refresh applications')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'orders-api' })).toBeInTheDocument();
  });

  it('shows access denied rather than an empty list on 403', async () => {
    const api = createFakeApi();
    api.applications.list.mockRejectedValue(httpError(403));
    renderApp('/applications', api);
    expect(await screen.findByRole('heading', { name: 'Access denied' })).toBeInTheDocument();
    expect(screen.queryByText('No applications yet')).not.toBeInTheDocument();
  });

  it('hides the create action from users without permission', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession(['audit:read']));
    renderApp('/applications', api);
    await screen.findByRole('link', { name: 'orders-api' });
    expect(screen.queryByRole('link', { name: 'Create application' })).not.toBeInTheDocument();
  });
});

describe('create application', () => {
  type User = ReturnType<typeof renderApp>['user'];

  async function next(user: User) {
    await user.click(screen.getByRole('button', { name: 'Next' }));
  }

  /** Walks the seven steps with valid values, using the defaults where they are valid. */
  async function fillToReview(user: User) {
    await user.type(await screen.findByLabelText(/Application name/), 'billing-api');
    await next(user);
    await user.type(await screen.findByLabelText(/Repository URL/), 'https://git.example.com/acme/billing.git');
    await next(user);
    await screen.findByText('Step 3 of 7: Runtime');
    await next(user);
    await screen.findByText('Step 4 of 7: Scaling');
    await next(user);
    await screen.findByText('Step 5 of 7: Environment');
    await next(user);
    await screen.findByText('Step 6 of 7: Target');
    await next(user);
    await screen.findByText('Step 7 of 7: Review');
  }

  it('requires a validated provider connection first', async () => {
    const api = createFakeApi();
    api.providers.list.mockResolvedValue([makeConnection({ status: 'pending_validation' })]);
    renderApp('/applications/new', api);
    expect(await screen.findByRole('heading', { name: 'Connect a provider first' })).toBeInTheDocument();
  });

  it('validates each step before moving on and keeps earlier values', async () => {
    const { user } = renderApp('/applications/new');
    await user.click(await screen.findByRole('button', { name: 'Next' }));
    expect(screen.getAllByText('Application name is required.').length).toBeGreaterThan(0);
    expect(screen.getByText('Step 1 of 7: Details')).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Application name/), 'billing-api');
    await next(user);
    expect(await screen.findByText('Step 2 of 7: Source')).toBeInTheDocument();
    await next(user);
    expect(screen.getAllByText('Repository URL is required.').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByLabelText(/Application name/)).toHaveValue('billing-api');
  });

  it('accepts an immutable commit only when it is a SHA', async () => {
    const { user } = renderApp('/applications/new');
    await user.type(await screen.findByLabelText(/Application name/), 'billing-api');
    await next(user);
    await user.type(await screen.findByLabelText(/Repository URL/), 'https://git.example.com/acme/billing.git');
    await user.selectOptions(screen.getByLabelText(/Reference type/), 'commit');
    await user.type(screen.getByLabelText(/Commit SHA/), 'not-a-sha');
    await next(user);
    expect(screen.getAllByText('Enter a commit SHA of 7 to 40 hexadecimal characters.').length).toBeGreaterThan(0);
    await user.clear(screen.getByLabelText(/Commit SHA/));
    await user.type(screen.getByLabelText(/Commit SHA/), '9b1c0de4f2a7');
    await next(user);
    expect(await screen.findByText('Step 3 of 7: Runtime')).toBeInTheDocument();
  });

  it('warns against secrets in environment variables and validates secret references', async () => {
    const { user } = renderApp('/applications/new');
    await user.type(await screen.findByLabelText(/Application name/), 'billing-api');
    await next(user);
    await user.type(await screen.findByLabelText(/Repository URL/), 'https://git.example.com/acme/billing.git');
    await next(user);
    await next(user);
    await next(user);
    expect(await screen.findByText('Do not put secrets in environment variables')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add variable' }));
    await user.type(screen.getByLabelText('Variable 1 name'), 'API_TOKEN');
    expect(screen.getByText(/This looks like a secret/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add secret reference' }));
    await user.type(screen.getByLabelText('Secret 1 variable name'), 'DATABASE_URL');
    await user.type(screen.getByLabelText('Secret 1 reference'), 'hunter2');
    await next(user);
    expect(screen.getAllByText(/Use an AWS Secrets Manager/).length).toBeGreaterThan(0);
    expect(screen.getByText('Step 5 of 7: Environment')).toBeInTheDocument();
  });

  it('shows provider capability warnings and blocks unsupported settings', async () => {
    const { user } = renderApp('/applications/new');
    await user.type(await screen.findByLabelText(/Application name/), 'billing-api');
    await user.click(screen.getByRole('radio', { name: /Background worker/ }));
    await next(user);
    await user.type(await screen.findByLabelText(/Repository URL/), 'https://git.example.com/acme/billing.git');
    await next(user);
    await next(user);
    const min = await screen.findByLabelText(/Minimum instances/);
    await user.clear(min);
    await user.type(min, '0');
    await next(user);
    await next(user);
    const warnings = await screen.findByRole('list', { name: 'Provider capability warnings' });
    expect(within(warnings).getByText(/does not run background worker applications/)).toBeInTheDocument();
    expect(within(warnings).getByText(/minimum instances will be 1, not 0/)).toBeInTheDocument();
    expect(within(warnings).getAllByText('Not supported:').length).toBeGreaterThan(0);
    expect(within(warnings).getByText('Will be adjusted:')).toBeInTheDocument();
    await next(user);
    expect(screen.getByText('Step 6 of 7: Target')).toBeInTheDocument();
    // "Edit" jumps straight to the step that owns the setting.
    await user.click(within(warnings).getAllByRole('button', { name: 'Edit' })[0]!);
    expect(await screen.findByText('Step 1 of 7: Details')).toBeInTheDocument();
  });

  it('lets the user continue when capabilities cannot be loaded, and says so', async () => {
    const api = createFakeApi();
    api.providers.getCapabilities.mockRejectedValue(httpError(503));
    const { user } = renderApp('/applications/new', api);
    await fillToReview(user);
    expect(screen.getByText(/capabilities were not available/)).toBeInTheDocument();
  });

  it('creates once, even when submit is clicked repeatedly, and reports success', async () => {
    const api = createFakeApi();
    let resolve: (v: Awaited<ReturnType<typeof api.applications.create>>) => void = () => undefined;
    api.applications.create.mockReturnValue(new Promise((r) => (resolve = r)));
    const { user, router } = renderApp('/applications/new', api);
    await fillToReview(user);
    const submit = screen.getByRole('button', { name: 'Create application' });
    await user.click(submit);
    await user.click(submit);
    expect(api.applications.create).toHaveBeenCalledTimes(1);
    expect(api.applications.create.mock.calls[0]?.[0]).toMatchObject({
      name: 'billing-api',
      application_type: 'web_service',
      connection_id: 'conn-1',
      region: 'eu-west-1',
      source: { type: 'repository', ref_type: 'branch', git_ref: 'main', build_context: '.' },
      exposure: 'public',
      concurrency: null,
    });
    expect(screen.getByRole('button', { name: /Creating/ })).toBeDisabled();
    resolve((await import('./helpers/fixtures')).makeApp({ id: 'app-new', name: 'billing-api' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/applications/app-new'));
    expect(await screen.findByText('Application created')).toBeInTheDocument();
  });

  it('lets the user edit an earlier step from the review', async () => {
    const { user } = renderApp('/applications/new');
    await fillToReview(user);
    await user.click(screen.getByRole('button', { name: 'Edit scaling and exposure' }));
    expect(await screen.findByText('Step 4 of 7: Scaling')).toBeInTheDocument();
  });

  it('maps server validation errors back to the right step and field', async () => {
    const api = createFakeApi();
    api.applications.create.mockRejectedValue(
      httpError(422, { method: 'POST', fieldErrors: [{ field: 'source.repository_url', message: 'Repository not reachable.' }] }),
    );
    const { user } = renderApp('/applications/new', api);
    await fillToReview(user);
    await user.click(screen.getByRole('button', { name: 'Create application' }));
    expect(await screen.findByText('Step 2 of 7: Source')).toBeInTheDocument();
    expect(screen.getByText('Repository not reachable.')).toBeInTheDocument();
    expect(screen.getByLabelText(/Repository URL/)).toHaveValue('https://git.example.com/acme/billing.git');
  });

  it('reuses the idempotency key when the same submission is retried after a timeout', async () => {
    const api = createFakeApi();
    api.applications.create.mockRejectedValueOnce(new ApiError({ kind: 'timeout', method: 'POST' }));
    const { user } = renderApp('/applications/new', api);
    await fillToReview(user);
    await user.click(screen.getByRole('button', { name: 'Create application' }));
    expect(await screen.findByText(/outcome is unknown/)).toBeInTheDocument();
    expect(screen.getByText(/Submitting again with the same values is safe/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create application' }));
    await waitFor(() => expect(api.applications.create).toHaveBeenCalledTimes(2));
    const [first, second] = api.applications.create.mock.calls;
    expect(first?.[1].idempotencyKey).toBe(second?.[1].idempotencyKey);
  });
});

describe('application details', () => {
  it('has Overview, Deployments, Configuration and Logs tabs and shows the image digest', async () => {
    const { user, router } = renderApp('/applications/app-1');
    const tabs = await screen.findByRole('navigation', { name: 'Application sections' });
    for (const name of ['Overview', 'Deployments', 'Configuration', 'Logs']) {
      expect(within(tabs).getByRole('link', { name })).toBeInTheDocument();
    }
    expect(screen.getByText(`sha256:${'a'.repeat(64)}`)).toBeInTheDocument();
    await user.click(within(tabs).getByRole('link', { name: 'Deployments' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/applications/app-1/deployments'));
    expect(await screen.findByRole('table', { name: 'Deployments of orders-api' })).toBeInTheDocument();
  });

  it('requires typing the name before deleting, and confirms only after the backend does', async () => {
    const api = createFakeApi();
    const { user, router } = renderApp('/applications/app-1', api);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete orders-api?' });
    const confirm = within(dialog).getByRole('button', { name: 'Delete application' });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Type/), 'orders-api');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(router.state.location.pathname).toBe('/applications'));
    expect(api.applications.remove).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('Application deleted')).toBeInTheDocument();
  });

  it('reports an uncertain delete instead of claiming success, and checks status on request', async () => {
    const api = createFakeApi();
    api.applications.remove.mockRejectedValue(new ApiError({ kind: 'timeout', method: 'DELETE' }));
    const { user } = renderApp('/applications/app-1', api);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('alertdialog');
    await user.type(within(dialog).getByLabelText(/Type/), 'orders-api');
    await user.click(within(dialog).getByRole('button', { name: 'Delete application' }));
    expect(await screen.findByText('Deletion status unknown')).toBeInTheDocument();
    expect(screen.queryByText('Application deleted')).not.toBeInTheDocument();
    api.applications.get.mockRejectedValue(httpError(404));
    await user.click(screen.getByRole('button', { name: 'Check current status' }));
    expect(await screen.findByText('The application was deleted')).toBeInTheDocument();
    expect(api.applications.remove).toHaveBeenCalledTimes(1);
  });

  it('queues a redeployment and says it was accepted, not completed', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue((await import('./helpers/fixtures')).makeDeployment({ id: 'dep-new-000000', status: 'queued' }));
    const { user, router } = renderApp('/applications/app-1', api);
    await user.click(await screen.findByRole('button', { name: 'Redeploy' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Start deployment' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/deployments/dep-new-000000'));
    expect(await screen.findByText('Deployment queued', { selector: '.toast__title' })).toBeInTheDocument();
    expect(api.deployments.create.mock.calls[0]?.[1].idempotencyKey).toBeTruthy();
  });

  it('only renders safe external service links', async () => {
    const api = createFakeApi();
    api.applications.get.mockResolvedValue((await import('./helpers/fixtures')).makeApp({ service_url: 'javascript:alert(1)' }));
    renderApp('/applications/app-1', api);
    expect(await screen.findByText('javascript:alert(1)')).not.toHaveAttribute('href');
  });

  it('explains when the backend does not provide runtime logs', async () => {
    const api = createFakeApi();
    api.applications.getLogs.mockRejectedValue(httpError(501));
    renderApp('/applications/app-1/logs', api);
    expect(await screen.findByRole('heading', { name: 'Runtime logs are not available' })).toBeInTheDocument();
  });

  it('redacts credentials that appear in runtime logs', async () => {
    const api = createFakeApi();
    api.applications.getLogs.mockResolvedValue({
      entries: [{ timestamp: '2026-10-01T10:00:00Z', stream: 'stderr', message: 'connect failed password=hunter2' }],
      truncated: false,
    });
    renderApp('/applications/app-1/logs', api);
    expect(await screen.findByRole('list', { name: 'Runtime logs' })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('hunter2');
  });
});

describe('configuration', () => {
  it('compares the requested configuration with what the provider runs', async () => {
    const api = createFakeApi();
    api.applications.getConfiguration.mockResolvedValue(
      makeConfig({
        min_instances: 0,
        effective: {
          port: 8080,
          cpu: '1',
          memory: '2Gi',
          min_instances: 1,
          max_instances: 3,
          concurrency: 100,
          exposure: 'public',
          health_check_path: '/healthz',
          observed_at: '2026-10-01T10:00:00Z',
        },
        adjustments: [{ field: 'min_instances', requested: '0', effective: '1', reason: 'App Runner keeps one instance running.' }],
      }),
    );
    renderApp('/applications/app-1/configuration', api);
    const table = await screen.findByRole('table', { name: /Requested configuration compared/ });
    const row = within(table).getByRole('row', { name: /Minimum instances/ });
    expect(row).toHaveTextContent('0');
    expect(row).toHaveTextContent('1');
    expect(row).toHaveTextContent('differs');
    expect(screen.getByText('App Runner keeps one instance running.')).toBeInTheDocument();
  });

  it('says when no effective configuration exists yet', async () => {
    renderApp('/applications/app-1/configuration');
    expect(await screen.findByText(/effective configuration is reported/)).toBeInTheDocument();
  });

  it('saves configuration, including secret references, with the loaded version', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/applications/app-1/configuration', api);
    const port = await screen.findByLabelText(/Container port/);
    await user.clear(port);
    await user.type(port, '9090');
    await user.click(screen.getByRole('button', { name: 'Add secret reference' }));
    await user.type(screen.getByLabelText('Secret 2 variable name'), 'STRIPE_KEY');
    await user.type(screen.getByLabelText('Secret 2 reference'), 'projects/acme/secrets/stripe/versions/latest');
    await user.click(screen.getByRole('button', { name: 'Save configuration' }));
    await waitFor(() => expect(api.applications.updateConfiguration).toHaveBeenCalledTimes(1));
    const body = api.applications.updateConfiguration.mock.calls[0]?.[1];
    expect(body).toMatchObject({ version: 3, port: 9090 });
    expect(body?.secret_refs).toEqual([
      { name: 'DATABASE_URL', reference: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:orders/db' },
      { name: 'STRIPE_KEY', reference: 'projects/acme/secrets/stripe/versions/latest' },
    ]);
    expect(await screen.findByText('Configuration saved')).toBeInTheDocument();
  });

  it('keeps edits and offers a reload on a version conflict', async () => {
    const api = createFakeApi();
    api.applications.updateConfiguration.mockRejectedValue(httpError(409, { method: 'PUT', code: 'version_conflict' }));
    const { user } = renderApp('/applications/app-1/configuration', api);
    const port = await screen.findByLabelText(/Container port/);
    await user.clear(port);
    await user.type(port, '9090');
    await user.click(screen.getByRole('button', { name: 'Save configuration' }));
    expect(await screen.findByText('The configuration changed since you opened it')).toBeInTheDocument();
    expect(screen.getByLabelText(/Container port/)).toHaveValue(9090);
  });

  it('validates the saved configuration against the provider on request', async () => {
    const api = createFakeApi();
    api.applications.validate.mockResolvedValue({
      valid: false,
      issues: [{ field: 'max_instances', severity: 'error', message: 'App Runner allows at most 25 instances.' }],
      adjustments: [],
    });
    const { user } = renderApp('/applications/app-1/configuration', api);
    await user.click(await screen.findByRole('button', { name: 'Validate saved configuration' }));
    expect(await screen.findByText('The configuration cannot be deployed as it is.')).toBeInTheDocument();
    expect(screen.getByText(/App Runner allows at most 25 instances/)).toBeInTheDocument();
  });

  it('is read-only for users who cannot update applications', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession(['audit:read']));
    renderApp('/applications/app-1/configuration', api);
    expect(await screen.findByText(/You can view this configuration but not change it/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save configuration' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add secret reference' })).not.toBeInTheDocument();
  });
});
