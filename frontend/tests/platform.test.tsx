import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../src/services/api/errors';
import { capabilityFindings } from '../src/utils/capabilities';
import { createFakeApi } from './helpers/fakeApi';
import {
  makeAlertChannel,
  makeApiToken,
  makeApp,
  makeAutomation,
  makeCapabilities,
  makeDeployment,
  makeDomain,
} from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('application automation', () => {
  it('turns on deploy-on-push and shows the webhook secret only once', async () => {
    const api = createFakeApi();
    api.applications.get.mockResolvedValue(
      makeApp({ source: { type: 'repository', image: null, repository_url: 'https://github.com/acme/orders.git', ref_type: 'branch', git_ref: 'main', dockerfile_path: 'Dockerfile', build_context: '.' } }),
    );
    const { user } = renderApp('/applications/app-1/automation', api);
    await user.click(await screen.findByLabelText(/Deploy every push/));
    await user.click(screen.getByRole('button', { name: 'Save automation settings' }));
    await waitFor(() => expect(api.platform.updateAutomation).toHaveBeenCalled());
    expect(api.platform.updateAutomation.mock.calls[0]?.[1]).toMatchObject({ auto_deploy: true, auto_deploy_branch: null });

    await user.click(screen.getByRole('button', { name: /webhook secret/ }));
    expect(await screen.findByText('whsec-test-000000000000')).toBeInTheDocument();
    expect(screen.getByText('Copy the secret now')).toBeInTheDocument();
  });

  it('explains that image applications cannot auto-deploy', async () => {
    renderApp('/applications/app-1/automation');
    expect(await screen.findByText(/deploys a container image/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Deploy every push/)).not.toBeInTheDocument();
  });

  it('promotes the running version to the target environment', async () => {
    const api = createFakeApi();
    api.applications.get.mockResolvedValue(makeApp({ current_version: 'r3' }));
    api.platform.getAutomation.mockResolvedValue(makeAutomation({ environment: 'staging', promote_to_id: 'app-prod' }));
    const { user } = renderApp('/applications/app-1/automation', api);
    await user.click(await screen.findByRole('button', { name: 'Promote r3' }));
    await waitFor(() => expect(api.platform.promote).toHaveBeenCalledWith('app-1', expect.objectContaining({ idempotencyKey: expect.any(String) })));
  });

  it('lets only approvers change the approval gate', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue({
      ...(await api.auth.getSession()),
      permissions: ['applications:update', 'deployments:create'],
    });
    renderApp('/applications/app-1/automation', api);
    expect(await screen.findByLabelText(/Require approval/)).toBeDisabled();
    expect(screen.getByText('Only owners and admins can change this.')).toBeInTheDocument();
  });

  it('pauses the application and saves a sleep schedule', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/applications/app-1/automation', api);
    await user.click(await screen.findByRole('button', { name: 'Pause now' }));
    await waitFor(() => expect(api.platform.pause).toHaveBeenCalledWith('app-1'));
    await user.click(screen.getByLabelText('Sat'));
    await user.click(screen.getByRole('button', { name: 'Save schedule' }));
    await waitFor(() => expect(api.platform.setSchedule).toHaveBeenCalled());
    expect(api.platform.setSchedule.mock.calls[0]?.[1]).toMatchObject({ days: [0, 1, 2, 3, 4, 5], sleep_at: '20:00', wake_at: '08:00' });
  });
});

describe('metrics and cost', () => {
  it('shows each measure with its numbers and the cost estimate', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/applications/app-1/metrics', api);
    const requests = await screen.findByRole('figure', { name: 'Requests' });
    expect(within(requests).getByText('42')).toBeInTheDocument(); // 12 + 30 over the range
    expect(screen.getByRole('figure', { name: 'Response time' })).toHaveTextContent('84.5 ms');
    expect((await screen.findAllByText(/\$10\.22/)).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: '24 hours' }));
    await waitFor(() => expect(api.platform.getMetrics).toHaveBeenLastCalledWith('app-1', '24h', expect.anything()));
  });

  it('says why metrics are unavailable instead of drawing empty charts', async () => {
    const api = createFakeApi();
    api.platform.getMetrics.mockResolvedValue({ range: '1h', step_seconds: 60, series: [], unavailable_reason: 'The application has not been deployed yet.' });
    renderApp('/applications/app-1/metrics', api);
    expect(await screen.findByText('The application has not been deployed yet.')).toBeInTheDocument();
  });
});

describe('custom domains', () => {
  it('adds a domain and lists the DNS records to create', async () => {
    const api = createFakeApi();
    api.applications.get.mockResolvedValue(makeApp({ current_version: 'r1' }));
    const { user } = renderApp('/applications/app-1/domains', api);
    await user.type(await screen.findByLabelText(/^Domain/), 'not a domain');
    await user.click(screen.getByRole('button', { name: 'Add domain' }));
    expect(screen.getAllByText(/Enter a domain you own/).length).toBeGreaterThan(0);
    expect(api.platform.addDomain).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText(/^Domain/));
    await user.type(screen.getByLabelText(/^Domain/), 'API.Example.com');
    await user.click(screen.getByRole('button', { name: 'Add domain' }));
    await waitFor(() => expect(api.platform.addDomain).toHaveBeenCalledWith('app-1', 'api.example.com'));
    expect(await screen.findByText('Waiting for DNS')).toBeInTheDocument();
    expect(screen.getByText('abc.eu-west-1.awsapprunner.com')).toBeInTheDocument();
  });

  it('tells the user when the provider has no custom domains', async () => {
    const api = createFakeApi();
    api.providers.getCapabilities.mockResolvedValue(makeCapabilities({ features: { repository_builds: false, metrics: true, custom_domains: false, pause: true } }));
    api.platform.listDomains.mockResolvedValue([makeDomain()]);
    renderApp('/applications/app-1/domains', api);
    expect(await screen.findByText('Not available for this provider')).toBeInTheDocument();
  });
});

describe('approvals', () => {
  it('lets an approver approve a waiting deployment', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(
      makeDeployment({ status: 'awaiting_approval', allowed_actions: { rollback: false, cancel: true, approve: true } }),
    );
    const { user } = renderApp('/deployments/dep-1', api);
    expect(await screen.findByText('Waiting for approval')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(api.deployments.approve).toHaveBeenCalledWith('dep-1'));
  });

  it('offers no approval when the backend does not permit it', async () => {
    const api = createFakeApi();
    api.deployments.get.mockResolvedValue(makeDeployment({ status: 'awaiting_approval' }));
    renderApp('/deployments/dep-1', api);
    expect(await screen.findByText('Waiting for approval')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  });
});

describe('alert channels and API tokens', () => {
  it('creates a signed webhook channel and shows its secret once', async () => {
    const api = createFakeApi();
    api.platform.listAlertChannels.mockResolvedValue([makeAlertChannel()]);
    const { user } = renderApp('/settings/alerts', api);
    expect(await screen.findByText('Ops')).toBeInTheDocument();
    expect(screen.getByText(/hooks\.slack\.com/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/^Type/), 'webhook');
    await user.type(screen.getByLabelText(/^Name/), 'Pager');
    await user.type(screen.getByLabelText(/Webhook address/), 'https://hooks.example.com/cb');
    await user.click(screen.getByRole('button', { name: 'Add channel' }));
    await waitFor(() => expect(api.platform.createAlertChannel).toHaveBeenCalled());
    expect(api.platform.createAlertChannel.mock.calls[0]?.[0]).toMatchObject({ kind: 'webhook', name: 'Pager', url: 'https://hooks.example.com/cb' });
    expect(await screen.findByText('sig-secret-0000')).toBeInTheDocument();
  });

  it('shows why the server refused a channel, next to the field', async () => {
    const api = createFakeApi();
    api.platform.createAlertChannel.mockRejectedValue(
      new ApiError({
        kind: 'http',
        status: 422,
        method: 'POST',
        code: 'validation_error',
        fieldErrors: [{ field: 'url', message: 'The webhook host must be a public internet address.' }],
      }),
    );
    const { user } = renderApp('/settings/alerts', api);
    await user.selectOptions(await screen.findByLabelText(/^Type/), 'webhook');
    await user.type(screen.getByLabelText(/^Name/), 'Internal');
    await user.type(screen.getByLabelText(/Webhook address/), 'https://10.0.0.5/hook');
    await user.click(screen.getByRole('button', { name: 'Add channel' }));
    // Highlighted on the field itself (linked as its description) and listed in the summary.
    const field = screen.getByLabelText(/Webhook address/);
    await waitFor(() => expect(field).toHaveAccessibleDescription(/must be a public internet address/));
    expect(within(screen.getByRole('alert')).getByText('The webhook host must be a public internet address.')).toBeInTheDocument();
  });

  it('creates and revokes API tokens', async () => {
    const api = createFakeApi();
    api.platform.listApiTokens.mockResolvedValue([makeApiToken()]);
    const { user } = renderApp('/settings/api-tokens', api);
    expect(await screen.findByText('cbt_abcdefgh…')).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Name/), 'GitHub Actions');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    expect(await screen.findByText('cbt_test-token-value')).toBeInTheDocument();
    expect(api.platform.createApiToken).toHaveBeenCalledWith({ name: 'GitHub Actions', expires_in_days: 90 });
    await user.click(screen.getByRole('button', { name: 'Revoke CI' }));
    await waitFor(() => expect(api.platform.revokeApiToken).toHaveBeenCalledWith('tok-1'));
  });
});

describe('capability rules', () => {
  const caps = makeCapabilities({ provider: 'kubernetes', supported_application_types: ['web_service', 'api', 'worker'], features: { repository_builds: false, metrics: true, custom_domains: false, pause: true } });
  const spec = {
    application_type: 'worker' as const,
    region: 'eu-west-1',
    cpu: '1',
    memory: '1Gi',
    min_instances: 0,
    max_instances: 2,
    concurrency: null,
    exposure: 'public' as const,
    secretRefCount: 0,
    sourceType: 'repository' as const,
  };

  it('flags builds the provider cannot run and worker settings that cannot work', () => {
    const fields = capabilityFindings(spec, caps).filter((f) => f.severity === 'error').map((f) => f.field);
    expect(fields).toEqual(expect.arrayContaining(['source_type', 'exposure', 'min_instances']));
  });
});

describe('provider setup for the new providers', () => {
  it('connects a Kubernetes cluster without any stored credential', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/providers/new?provider=kubernetes', api);
    expect(await screen.findByText(/Do not paste a kubeconfig/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Connection name/), 'AKS');
    await user.selectOptions(screen.getByLabelText(/Default region/), 'in-cluster');
    await user.type(screen.getByLabelText(/API server/), 'https://aks.example.azmk8s.io:443');
    await user.type(screen.getByLabelText(/Cluster CA certificate/), '-----BEGIN PRIVATE KEY-----');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));
    expect(screen.getAllByText(/looks like a private key/).length).toBeGreaterThan(0);
    expect(api.providers.create).not.toHaveBeenCalled();
  });

  it('shows ECS Express Mode with its own account and role fields', async () => {
    renderApp('/providers/new?provider=aws_ecs_express');
    expect(await screen.findByLabelText(/AWS account ID/)).toBeInTheDocument();
    expect(screen.getByLabelText(/ECS cluster/)).toBeInTheDocument();
  });
});
