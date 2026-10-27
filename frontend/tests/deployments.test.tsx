import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DeploymentProgress } from '../src/components/status/DeploymentProgress';
import { StatusBadge } from '../src/components/status/StatusBadge';
import { deploymentStatus } from '../src/components/status/statusMaps';
import { DEPLOYMENT_STATUSES } from '../src/services/api/decoders';
import { createFakeApi, httpError } from './helpers/fakeApi';
import { makeDeployment, makeSession, page } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('DeploymentProgress', () => {
  it('labels every stage state in text, not only colour', () => {
    render(
      <DeploymentProgress
        stages={[
          { name: 'queued', status: 'succeeded', started_at: null, finished_at: null },
          { name: 'validation', status: 'succeeded', started_at: null, finished_at: null },
          { name: 'build', status: 'failed', started_at: null, finished_at: null },
          { name: 'publishing', status: 'skipped', started_at: null, finished_at: null },
          { name: 'provisioning', status: 'cancelled', started_at: null, finished_at: null },
          { name: 'verification', status: 'pending', started_at: null, finished_at: null },
        ]}
      />,
    );
    const list = screen.getByRole('list', { name: 'Deployment stages' });
    expect(within(list).getAllByText(/Completed/)).toHaveLength(2);
    expect(within(list).getByText(/Failed/)).toBeInTheDocument();
    expect(within(list).getByText(/Skipped/)).toBeInTheDocument();
    expect(within(list).getByText(/Cancelled/)).toBeInTheDocument();
    expect(within(list).getByText(/Pending/)).toBeInTheDocument();
  });

  it('marks the confirmed current stage and never shows an invented percentage', () => {
    render(<DeploymentProgress stages={makeDeployment().stages} progressPercent={null} />);
    const current = screen.getByRole('listitem', { current: 'step' });
    expect(current).toHaveTextContent('Build');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('shows real provider progress when reported', () => {
    render(<DeploymentProgress stages={makeDeployment().stages} progressPercent={42} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
  });
});

describe('deployment states', () => {
  it('gives every backend state a distinct text label', () => {
    const states = DEPLOYMENT_STATUSES.filter((s) => s !== 'unknown');
    expect(states).toEqual([
      'awaiting_approval',
      'queued',
      'validating',
      'building',
      'publishing',
      'deploying',
      'verifying',
      'succeeded',
      'failed',
      'rolling_back',
      'rolled_back',
      'failed_rollback',
      'cancelled',
    ]);
    render(
      <ul>
        {states.map((s) => (
          <li key={s}>
            <StatusBadge status={deploymentStatus[s]} />
          </li>
        ))}
      </ul>,
    );
    const labels = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(new Set(labels).size).toBe(states.length);
    expect(labels).toContain('Rollback failed');
  });

  it('explains a deployment that was rolled back after failing verification', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(
      makeDeployment({
        status: 'rolled_back',
        finished_at: '2026-10-01T10:05:00Z',
        failure: { code: 'verification_failed', message: 'Health checks failed.' },
      }),
    );
    renderApp('/deployments/dep-12345678abcd', api);
    expect(await screen.findByText('Deployment rolled back')).toBeInTheDocument();
    expect(screen.getByText('Health checks failed.')).toBeInTheDocument();
  });

  it('shows deployment logs, or says when the backend has none', async () => {
    const api = createFakeApi();
    api.deployments.getLogs.mockRejectedValue(httpError(404));
    renderApp('/deployments/dep-12345678abcd', api);
    expect(await screen.findByRole('heading', { name: 'Deployment logs are not available' })).toBeInTheDocument();
  });
});

describe('deployment details', () => {
  it('polls while active and announces the transition to success once', async () => {
    const api = createFakeApi();
    // The status changes only when the test says so, so a slow machine cannot skip the building state.
    let finished = false;
    api.deployments.get.mockImplementation(async () =>
      finished
        ? makeDeployment({ status: 'succeeded', finished_at: '2026-10-01T10:05:00Z', stages: makeDeployment().stages.map((s) => ({ ...s, status: 'succeeded' as const })) })
        : makeDeployment({ status: 'building' }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderApp('/deployments/dep-12345678abcd', api);
      expect(await screen.findByText('Deployment: building')).toBeInTheDocument();
      finished = true;
      await vi.advanceTimersByTimeAsync(3500);
      expect(await screen.findByText('Deployment succeeded', { selector: '.alert__title' })).toBeInTheDocument();
      await waitFor(() => expect(screen.getByTestId('announcer')).toHaveTextContent('Deployment succeeded'));
      const calls = api.deployments.get.mock.calls.length;
      await vi.advanceTimersByTimeAsync(10_000);
      expect(api.deployments.get.mock.calls.length).toBe(calls);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not notify about a status that was already final on first load', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(makeDeployment({ status: 'failed', failure: { code: 'build_failed', message: 'npm ci failed with token=abc123' } }));
    renderApp('/deployments/dep-12345678abcd', api);
    expect(await screen.findByText('Failure reason')).toBeInTheDocument();
    expect(screen.queryByText('Deployment failed', { selector: '.toast__title' })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('abc123');
  });

  it('asks for confirmation before cancelling', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(makeDeployment({ allowed_actions: { rollback: false, cancel: true, approve: false } }));
    const { user } = renderApp('/deployments/dep-12345678abcd', api);
    await user.click(await screen.findByRole('button', { name: 'Cancel deployment' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Cancel this deployment?' });
    expect(api.deployments.cancel).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel deployment' }));
    await waitFor(() => expect(api.deployments.cancel).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Cancellation requested')).toBeInTheDocument();
  });

  it('starts a rollback when the backend permits it, and opens the rollback', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(
      makeDeployment({ status: 'succeeded', finished_at: '2026-10-01T10:05:00Z', allowed_actions: { rollback: true, cancel: false, approve: false } }),
    );
    const { user, router } = renderApp('/deployments/dep-12345678abcd', api);
    await user.click(await screen.findByRole('button', { name: 'Roll back to this revision' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Start rollback' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/deployments/dep-rb-0000000'));
    expect(await screen.findByText('Rollback started')).toBeInTheDocument();
  });

  it('does not offer a rollback the backend has not permitted, even to an administrator', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(makeDeployment({ status: 'succeeded', finished_at: '2026-10-01T10:05:00Z' }));
    renderApp('/deployments/dep-12345678abcd', api);
    expect(await screen.findByText('Deployment succeeded', { selector: '.alert__title' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Roll back to this revision' })).not.toBeInTheDocument();
  });

  it('shows a conflict inside the dialog without closing it', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(makeDeployment({ allowed_actions: { rollback: false, cancel: true, approve: false } }));
    api.deployments.cancel.mockRejectedValue(httpError(409, { method: 'POST', serverMessage: 'This deployment has already finished.' }));
    const { user } = renderApp('/deployments/dep-12345678abcd', api);
    await user.click(await screen.findByRole('button', { name: 'Cancel deployment' }));
    const dialog = await screen.findByRole('alertdialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel deployment' }));
    expect(await within(dialog).findByText('This deployment has already finished.')).toBeInTheDocument();
  });

  it('hides actions the user cannot perform even when the backend allows them', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession([]));
    api.deployments.get.mockResolvedValue(makeDeployment({ allowed_actions: { rollback: true, cancel: true, approve: false } }));
    renderApp('/deployments/dep-12345678abcd', api);
    await screen.findByText('Deployment: building');
    expect(screen.queryByRole('button', { name: 'Cancel deployment' })).not.toBeInTheDocument();
  });

  it('shows a not-found page for unknown deployments', async () => {
    const api = createFakeApi();
    api.deployments.get.mockRejectedValue(httpError(404));
    renderApp('/deployments/nope', api);
    expect(await screen.findByRole('heading', { name: 'Deployment not found' })).toBeInTheDocument();
  });
});

describe('deployments list', () => {
  it('filters by status through the URL', async () => {
    const api = createFakeApi();
    const { user, router } = renderApp('/deployments', api);
    await screen.findByRole('table', { name: 'Deployments' });
    await user.selectOptions(screen.getByLabelText('Status'), 'failed_rollback');
    await waitFor(() => expect(router.state.location.search).toContain('status=failed_rollback'));
    await waitFor(() =>
      expect(api.deployments.list).toHaveBeenLastCalledWith(expect.objectContaining({ status: ['failed_rollback'] }), expect.anything()),
    );
  });

  it('expands a status group from the URL, e.g. links from the overview', async () => {
    const api = createFakeApi();
    renderApp('/deployments?status=failed', api);
    await screen.findByRole('table', { name: 'Deployments' });
    expect(api.deployments.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: ['failed', 'failed_rollback'] }),
      expect.anything(),
    );
  });

  it('filters by provider, searches and sorts', async () => {
    const api = createFakeApi();
    const { user, router } = renderApp('/deployments', api);
    await screen.findByRole('table', { name: 'Deployments' });
    await user.selectOptions(screen.getByLabelText('Provider'), 'gcp_cloud_run');
    await user.type(screen.getByLabelText('Search deployments'), 'orders');
    await user.click(screen.getByRole('button', { name: /^Application/ }));
    await waitFor(() =>
      expect(api.deployments.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ provider: 'gcp_cloud_run', search: 'orders', sort: { key: 'application_name', direction: 'asc' } }),
        expect.anything(),
      ),
    );
    expect(router.state.location.search).toContain('sort=application_name');
    expect(screen.getByRole('columnheader', { name: /Application/ })).toHaveAttribute('aria-sort', 'ascending');
  });

  it('shows version, digest, provider, duration and who triggered it', async () => {
    const api = createFakeApi();
    api.deployments.list.mockResolvedValue(
      page([
        makeDeployment({
          status: 'succeeded',
          version: '2.3.1',
          image_digest: `sha256:${'b'.repeat(64)}`,
          finished_at: '2026-10-01T10:04:30Z',
        }),
      ]),
    );
    renderApp('/deployments', api);
    const table = await screen.findByRole('table', { name: 'Deployments' });
    expect(within(table).getByText('2.3.1')).toBeInTheDocument();
    expect(within(table).getByText(/sha256:bbbbbbbbbbbb/)).toBeInTheDocument();
    expect(within(table).getByText('AWS App Runner')).toBeInTheDocument();
    expect(within(table).getByText('4m 30s')).toBeInTheDocument();
    expect(within(table).getByText('Ada Admin')).toBeInTheDocument();
  });

  it('offers to clear filters when nothing matches', async () => {
    const api = createFakeApi();
    api.deployments.list.mockResolvedValue(page([]));
    renderApp('/deployments?status=cancelled', api);
    expect(await screen.findByRole('heading', { name: 'No matching deployments' })).toBeInTheDocument();
  });
});
