import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConnectionProgress } from '../src/components/status/ConnectionProgress';
import { ApiError } from '../src/services/api/errors';
import { createFakeApi, httpError } from './helpers/fakeApi';
import { makeConnection } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('ConnectionProgress', () => {
  it('is indeterminate when the backend reports no stage', () => {
    render(<ConnectionProgress status="validating" stage={null} />);
    const bar = screen.getByRole('progressbar', { name: 'Validating connection' });
    expect(bar).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByText('Connecting')).not.toBeInTheDocument();
  });

  it('advances only to the reported stage', () => {
    render(<ConnectionProgress status="validating" stage="validating_permissions" />);
    expect(screen.getByText('Connecting').closest('li')).toHaveTextContent('completed');
    expect(screen.getByText('Validating permissions').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Checking readiness').closest('li')).toHaveTextContent('not started');
  });

  it('never shows completed steps for a failed validation without a reported stage', () => {
    render(<ConnectionProgress status="error" stage={null} />);
    expect(screen.queryByText(/: completed/)).not.toBeInTheDocument();
  });
});

describe('provider connection page', () => {
  it('explains each failure without exposing credential material', async () => {
    const api = createFakeApi();
    api.providers.get.mockResolvedValue(
      makeConnection({
        status: 'insufficient_permissions',
        validation: {
          stage: null,
          started_at: null,
          finished_at: null,
          error_code: 'insufficient_permissions',
          checks: [{ name: 'App Runner permissions', status: 'failed', detail: 'Denied for key AKIAABCDEFGHIJKLMNOP' }],
        },
      }),
    );
    renderApp('/providers/conn-1', api);
    expect(await screen.findByText('Required permissions are missing')).toBeInTheDocument();
    expect(screen.getByText(/App Runner permissions/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('AKIAABCDEFGHIJKLMNOP');
  });

  it('runs validation, follows the reported stages and reports success only from the backend', async () => {
    const api = createFakeApi();
    // The backend state changes only when the test says so, so a slow machine cannot skip a stage.
    let backend: 'pending' | 'validating' | 'connected' = 'pending';
    api.providers.get.mockImplementation(async () =>
      backend === 'pending'
        ? makeConnection({ status: 'pending_validation', last_validated_at: null })
        : backend === 'validating'
          ? makeConnection({ status: 'validating', validation: { stage: 'checking_readiness', started_at: null, finished_at: null, checks: [], error_code: null } })
          : makeConnection({ status: 'connected' }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user } = renderApp('/providers/conn-1', api);
      const validate = await screen.findByRole('button', { name: 'Validate connection' });
      backend = 'validating';
      await user.click(validate);
      expect(await screen.findByText('Validating connection', { selector: '.alert__title' })).toBeInTheDocument();
      expect(screen.queryByText('Provider connected')).not.toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(2500);
      await screen.findByText('Checking readiness');
      expect(screen.queryByText('Provider connected')).not.toBeInTheDocument();
      backend = 'connected';
      await vi.advanceTimersByTimeAsync(2500);
      expect(await screen.findByText('Connection verified')).toBeInTheDocument();
      await waitFor(() => expect(screen.getAllByText('Provider connected', { selector: '.toast__title' })).toHaveLength(1));
      // The announcer sets its text after a short delay, so wait for it rather than racing it.
      await waitFor(() => expect(screen.getByTestId('announcer')).toHaveTextContent('Provider connected'));
    } finally {
      vi.useRealTimers();
    }
  });

  it('treats a timed-out validate request as uncertain and rechecks with a read, not another validate', async () => {
    const api = createFakeApi();
    api.providers.get.mockResolvedValue(makeConnection({ status: 'pending_validation' }));
    api.providers.validate.mockRejectedValue(new ApiError({ kind: 'timeout', method: 'POST' }));
    const { user } = renderApp('/providers/conn-1', api);
    await user.click(await screen.findByRole('button', { name: 'Validate connection' }));
    expect(await screen.findByText('The validation request did not complete')).toBeInTheDocument();
    const reads = api.providers.get.mock.calls.length;
    await user.click(screen.getAllByRole('button', { name: 'Check status' })[0]!);
    await waitFor(() => expect(api.providers.get.mock.calls.length).toBeGreaterThan(reads));
    expect(api.providers.validate).toHaveBeenCalledTimes(1);
  });

  it('confirms disconnection only after the backend does', async () => {
    const api = createFakeApi();
    let finish: () => void = () => undefined;
    api.providers.remove.mockReturnValue(new Promise((r) => (finish = () => r('deleted'))));
    const { user, router } = renderApp('/providers/conn-1', api);
    await user.click(await screen.findByRole('button', { name: 'Disconnect' }));
    const dialog = await screen.findByRole('alertdialog');
    await user.type(within(dialog).getByLabelText(/Type/), 'Production AWS');
    await user.click(within(dialog).getByRole('button', { name: 'Disconnect' }));
    expect(await within(dialog).findByRole('button', { name: /Disconnecting/ })).toBeDisabled();
    expect(screen.queryByText('Provider disconnected')).not.toBeInTheDocument();
    finish();
    await waitFor(() => expect(router.state.location.pathname).toBe('/providers'));
    expect(await screen.findByText('Provider disconnected')).toBeInTheDocument();
  });

  it('shows a conflict when the connection is still in use', async () => {
    const api = createFakeApi();
    api.providers.remove.mockRejectedValue(httpError(409, { method: 'DELETE', serverMessage: 'Applications still use this connection.' }));
    const { user } = renderApp('/providers/conn-1', api);
    await user.click(await screen.findByRole('button', { name: 'Disconnect' }));
    const dialog = await screen.findByRole('alertdialog');
    await user.type(within(dialog).getByLabelText(/Type/), 'Production AWS');
    await user.click(within(dialog).getByRole('button', { name: 'Disconnect' }));
    expect(await within(dialog).findByText('Applications still use this connection.')).toBeInTheDocument();
  });
});

describe('provider setup', () => {
  it('asks for identifiers only, never secret keys, and validates them', async () => {
    const { user } = renderApp('/providers/new?provider=gcp_cloud_run');
    expect(await screen.findByText('No secret keys are needed')).toBeInTheDocument();
    expect(screen.queryByLabelText(/secret/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));
    expect(screen.getAllByText(/Project IDs are 6–30/).length).toBeGreaterThan(0);
  });

  it('catches a role ARN from a different AWS account', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/providers/new?provider=aws_app_runner', api);
    await user.type(await screen.findByLabelText(/Connection name/), 'Prod');
    await user.selectOptions(screen.getByLabelText(/Default region/), 'us-east-1');
    await user.type(screen.getByLabelText(/AWS account ID/), '111111111111');
    await user.type(screen.getByLabelText(/IAM role ARN/), 'arn:aws:iam::222222222222:role/Deploy');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));
    expect(screen.getAllByText(/different account/).length).toBeGreaterThan(0);
    expect(api.providers.create).not.toHaveBeenCalled();
  });

  it('connects Azure with an app registration and no client secret', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/providers/new?provider=azure_container_apps', api);
    expect(await screen.findByText(/Do not create or paste client secrets/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/secret/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/Connection name/), 'Prod Azure');
    await user.selectOptions(screen.getByLabelText(/Default region/), 'eastasia');
    await user.type(screen.getByLabelText(/Tenant ID/), 'not-a-guid');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));
    expect(screen.getAllByText(/Tenant ID is a GUID/).length).toBeGreaterThan(0);
    expect(api.providers.create).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText(/Tenant ID/));
    await user.type(screen.getByLabelText(/Tenant ID/), '11111111-1111-1111-1111-111111111111');
    await user.type(screen.getByLabelText(/Subscription ID/), '22222222-2222-2222-2222-222222222222');
    await user.type(screen.getByLabelText(/Application \(client\) ID/), '33333333-3333-3333-3333-333333333333');
    await user.type(screen.getByLabelText(/Resource group/), 'apps-rg');
    await user.type(screen.getByLabelText(/Container Apps environment/), 'apps-env');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));
    await waitFor(() => expect(api.providers.create).toHaveBeenCalled());
    expect(api.providers.create.mock.calls[0]?.[0]).toEqual({
      name: 'Prod Azure',
      provider: 'azure_container_apps',
      default_region: 'eastasia',
      azure: {
        tenant_id: '11111111-1111-1111-1111-111111111111',
        subscription_id: '22222222-2222-2222-2222-222222222222',
        client_id: '33333333-3333-3333-3333-333333333333',
        resource_group: 'apps-rg',
        environment_name: 'apps-env',
        registry_name: null,
      },
    });
  });

  it('shows the federated credential an Azure connection must trust', async () => {
    const api = createFakeApi();
    api.providers.get.mockResolvedValue(
      makeConnection({
        provider: 'azure_container_apps',
        status: 'pending_validation',
        last_validated_at: null,
        config: {
          account_id: null,
          role_arn: null,
          project_id: null,
          workload_identity_provider: null,
          service_account_email: null,
          tenant_id: '11111111-1111-1111-1111-111111111111',
          subscription_id: '22222222-2222-2222-2222-222222222222',
          client_id: '33333333-3333-3333-3333-333333333333',
          resource_group: 'apps-rg',
          environment_name: 'apps-env',
          registry_name: 'acmeregistry',
          ecs_cluster: null,
          api_server: null,
          namespace: null,
          kubernetes_auth: null,
          token_audience: null,
        },
        setup: {
          external_id: null,
          trusted_principal: 'arn:aws:iam::999999999999:role/cloudbridge',
          federation_issuer: 'https://abc.tokens.sts.global.api.aws',
          federation_audience: 'api://AzureADTokenExchange',
          aws: null,
        },
      }),
    );
    renderApp('/providers/conn-1', api);
    expect(await screen.findByText('https://abc.tokens.sts.global.api.aws')).toBeInTheDocument();
    expect(screen.getAllByText('arn:aws:iam::999999999999:role/cloudbridge').length).toBeGreaterThan(0);
    expect(screen.getAllByText('api://AzureADTokenExchange').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Container Apps Contributor').length).toBeGreaterThan(0);
    expect(screen.getByText('22222222-2222-2222-2222-222222222222')).toBeInTheDocument();
  });
});
