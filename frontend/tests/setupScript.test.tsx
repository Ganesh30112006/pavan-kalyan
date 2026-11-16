import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  connectionCommand,
  defaultRoleArn,
  downloadAndRun,
  launchStackUrl,
  newConnectionCommand,
  shellArg,
  type NewConnectionValues,
} from '../src/pages/providers/setupCommand';
import type { ConnectionSetup, ProviderConnection } from '../src/types/api';
import { createFakeApi } from './helpers/fakeApi';
import { makeConnection } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

const ISSUER = 'https://abc.tokens.sts.global.api.aws';
const SUBJECT = 'arn:aws:iam::999999999999:role/cloudbridge';
const FEDERATED: ConnectionSetup = {
  external_id: null,
  trusted_principal: SUBJECT,
  federation_issuer: ISSUER,
  federation_audience: 'api://AzureADTokenExchange',
  aws: null,
  cloudformation_template_url: null,
};

function azureConnection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
  const base = makeConnection();
  return makeConnection({
    provider: 'azure_container_apps',
    status: 'pending_validation',
    default_region: 'eastus',
    config: {
      ...base.config,
      account_id: null,
      role_arn: null,
      tenant_id: '11111111-1111-1111-1111-111111111111',
      subscription_id: '22222222-2222-2222-2222-222222222222',
      client_id: '33333333-3333-3333-3333-333333333333',
      resource_group: 'apps-rg',
      environment_name: 'apps-env',
      registry_name: 'acmeregistry',
    },
    setup: FEDERATED,
    ...overrides,
  });
}

const EMPTY: NewConnectionValues = {
  default_region: '',
  tenant_id: '',
  subscription_id: '',
  resource_group: '',
  environment_name: '',
  registry_name: '',
  api_server: '',
  namespace: '',
};

describe('setup script commands', () => {
  it('quotes only what a shell would misread', () => {
    expect(shellArg('arn:aws:iam::123456789012:role/CloudBridge')).toBe('arn:aws:iam::123456789012:role/CloudBridge');
    expect(shellArg('https://abc.tokens.sts.global.api.aws')).toBe('https://abc.tokens.sts.global.api.aws');
    expect(shellArg('two words')).toBe("'two words'");
    expect(shellArg("it's; rm -rf /")).toBe(`'it'\\''s; rm -rf /'`);
    expect(shellArg('$(whoami)')).toBe("'$(whoami)'");
  });

  it('builds the AWS command from the connection, adding role names only when they differ', () => {
    expect(connectionCommand(makeConnection({ status: 'pending_validation' }))).toBe(
      'python3 cloudbridge-connect.py aws --role-arn arn:aws:iam::123456789012:role/CloudBridge ' +
        '--principal arn:aws:iam::999999999999:role/cloudbridge --external-id ext-123 --account 123456789012',
    );
    const base = makeConnection();
    const ecs = makeConnection({
      provider: 'aws_ecs_express',
      setup: { ...base.setup!, aws: { ...base.setup!.aws!, codebuild_role: 'AcmeBuild', apprunner_access_role: 'Ignored' } },
    });
    expect(connectionCommand(ecs)).toMatch(/ --ecs --codebuild-role AcmeBuild$/);
  });

  it('has no AWS command without the external ID', () => {
    const base = makeConnection();
    expect(connectionCommand(makeConnection({ setup: { ...base.setup!, external_id: null } }))).toBeNull();
  });

  it('builds the Azure, AKS and OIDC commands, and none for Google Cloud', () => {
    expect(connectionCommand(azureConnection())).toBe(
      'python3 cloudbridge-connect.py azure --client-id 33333333-3333-3333-3333-333333333333 ' +
        '--tenant 11111111-1111-1111-1111-111111111111 --subscription 22222222-2222-2222-2222-222222222222 ' +
        `--resource-group apps-rg --environment apps-env --location eastus --registry acmeregistry --issuer ${ISSUER} --subject ${SUBJECT}`,
    );
    const base = makeConnection();
    const aks = makeConnection({
      provider: 'kubernetes',
      config: {
        ...base.config,
        api_server: 'https://aks.hcp.eastus.azmk8s.io:443',
        namespace: 'apps',
        kubernetes_auth: 'aks',
        tenant_id: '11111111-1111-1111-1111-111111111111',
        client_id: '33333333-3333-3333-3333-333333333333',
      },
      setup: FEDERATED,
    });
    expect(connectionCommand(aks)).toBe(
      'python3 cloudbridge-connect.py aks --api-server https://aks.hcp.eastus.azmk8s.io:443 --namespace apps ' +
        `--client-id 33333333-3333-3333-3333-333333333333 --tenant 11111111-1111-1111-1111-111111111111 --issuer ${ISSUER} --subject ${SUBJECT}`,
    );
    const oidc = makeConnection({
      provider: 'kubernetes',
      config: { ...base.config, api_server: 'https://k8s.acme.test', namespace: 'apps', kubernetes_auth: 'oidc', token_audience: 'cloudbridge' },
      setup: { ...FEDERATED, federation_audience: 'cloudbridge' },
    });
    expect(connectionCommand(oidc)).toBe(
      `python3 cloudbridge-connect.py kubernetes --namespace apps --issuer ${ISSUER} --subject ${SUBJECT} --audience cloudbridge`,
    );
    expect(connectionCommand(makeConnection({ provider: 'gcp_cloud_run' }))).toBeNull();
  });

  it('starts a new Azure connection from the form, with defaults for names left empty', () => {
    expect(newConnectionCommand('azure_container_apps', FEDERATED, EMPTY, 'https://cb.test')).toBeNull();
    expect(newConnectionCommand('azure_container_apps', FEDERATED, { ...EMPTY, default_region: 'eastus' }, 'https://cb.test')).toBe(
      'python3 cloudbridge-connect.py azure --resource-group cloudbridge-apps --environment cloudbridge-env --location eastus ' +
        `--issuer ${ISSUER} --subject ${SUBJECT} --cloudbridge-url https://cb.test`,
    );
    expect(newConnectionCommand('kubernetes', FEDERATED, EMPTY, 'https://cb.test')).toBe(
      'python3 cloudbridge-connect.py aks --cluster CLUSTER_NAME --cluster-resource-group CLUSTER_RESOURCE_GROUP ' +
        `--namespace cloudbridge --issuer ${ISSUER} --subject ${SUBJECT} --cloudbridge-url https://cb.test`,
    );
    expect(newConnectionCommand('azure_container_apps', { ...FEDERATED, federation_issuer: null }, { ...EMPTY, default_region: 'eastus' }, 'x')).toBeNull();
  });
});

describe('setup script on the provider pages', () => {
  it('gives an unconnected AWS connection a ready-to-run command next to the manual steps', async () => {
    const api = createFakeApi();
    api.providers.get.mockResolvedValue(makeConnection({ status: 'pending_validation', last_validated_at: null }));
    renderApp('/providers/conn-1', api);

    const command = await screen.findByText(/^python3 cloudbridge-connect\.py aws /);
    expect(command).toHaveTextContent('--external-id ext-123');
    // One paste downloads the script and runs exactly that command.
    expect(screen.getByText(/^curl -fsSLO /)).toHaveTextContent(
      new RegExp(`^curl -fsSLO http://localhost(:\\d+)?/cloudbridge-connect\\.py && ${command.textContent!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`),
    );
    expect(screen.getByRole('link', { name: 'AWS CloudShell' })).toHaveAttribute('href', 'https://console.aws.amazon.com/cloudshell/home');
    expect(screen.getByRole('link', { name: /Download cloudbridge-connect\.py/ })).toHaveAttribute('href', '/cloudbridge-connect.py');
    expect(screen.getByRole('button', { name: 'Copy setup command' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy run command' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Or by hand' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy trust policy' })).toBeInTheDocument();
  });

  it('validates from the end of the script steps', async () => {
    const api = createFakeApi();
    const pending = makeConnection({ status: 'pending_validation', last_validated_at: null });
    const validating = { ...pending, status: 'validating' as const };
    let started = false;
    api.providers.get.mockImplementation(async () => (started ? validating : pending));
    api.providers.validate.mockImplementation(async () => {
      started = true;
      return validating;
    });
    const { user } = renderApp('/providers/conn-1', api);

    await user.click(await screen.findByRole('button', { name: 'Validate now' }));
    expect(api.providers.validate).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('Validating connection', { selector: '.alert__title' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate now' })).toBeDisabled();
  });

  it('offers a Launch Stack link with the connection’s values when CloudBridge has a template', async () => {
    const template = 'https://cb-templates.s3.ap-south-1.amazonaws.com/apprunner.json';
    const base = makeConnection({ status: 'pending_validation', last_validated_at: null, default_region: 'ap-south-1' });
    const withTemplate = makeConnection({ ...base, setup: { ...base.setup!, cloudformation_template_url: template } });

    const url = new URL(launchStackUrl(withTemplate)!);
    expect(`${url.origin}${url.pathname}${url.search}`).toBe('https://console.aws.amazon.com/cloudformation/home?region=ap-south-1');
    const [route, query] = url.hash.split('?');
    expect(route).toBe('#/stacks/quickcreate');
    expect(Object.fromEntries(new URLSearchParams(query))).toEqual({
      templateURL: template,
      stackName: 'CloudBridge-CloudBridge',
      param_CloudBridgePrincipal: 'arn:aws:iam::999999999999:role/cloudbridge',
      param_ExternalId: 'ext-123',
      param_RoleName: 'CloudBridge',
    });
    // Not offered without a template, for a role under a path, or outside the standard partition.
    expect(launchStackUrl(base)).toBeNull();
    expect(launchStackUrl(makeConnection({ ...withTemplate, config: { ...base.config, role_arn: 'arn:aws:iam::123456789012:role/team/CloudBridge' } }))).toBeNull();
    expect(launchStackUrl(makeConnection({ ...withTemplate, config: { ...base.config, role_arn: 'arn:aws-cn:iam::123456789012:role/CloudBridge' } }))).toBeNull();

    const api = createFakeApi();
    api.providers.get.mockResolvedValue(withTemplate);
    const { unmount } = renderApp('/providers/conn-1', api);
    const link = await screen.findByRole('link', { name: 'the prepared CloudBridge stack' });
    expect(link).toHaveAttribute('href', launchStackUrl(withTemplate));
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('heading', { name: 'Or with the setup script' })).toBeInTheDocument();
    unmount();

    api.providers.get.mockResolvedValue(base);
    renderApp('/providers/conn-1', api);
    expect(await screen.findByRole('heading', { name: 'With the setup script' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'the prepared CloudBridge stack' })).not.toBeInTheDocument();
  });

  it('suggests a role ARN only for a complete account ID, and quotes the download URL when needed', () => {
    expect(defaultRoleArn(' 123456789012 ')).toBe('arn:aws:iam::123456789012:role/CloudBridgeDeploy');
    expect(defaultRoleArn('12345678901')).toBeNull();
    expect(defaultRoleArn('12345678901a')).toBeNull();
    expect(downloadAndRun('https://cb.test/cloudbridge-connect.py', 'python3 x.py aws')).toBe(
      'curl -fsSLO https://cb.test/cloudbridge-connect.py && python3 x.py aws',
    );
    expect(downloadAndRun('https://cb.test/a b/cloudbridge-connect.py', 'run')).toBe("curl -fsSLO 'https://cb.test/a b/cloudbridge-connect.py' && run");
  });

  it('shows no script once connected, and only manual steps for Google Cloud', async () => {
    const api = createFakeApi();
    api.providers.get.mockResolvedValue(makeConnection({ status: 'connected' }));
    const { unmount } = renderApp('/providers/conn-1', api);
    expect(await screen.findByText('Details')).toBeInTheDocument();
    expect(screen.queryByText(/cloudbridge-connect\.py/)).not.toBeInTheDocument();
    unmount();

    api.providers.get.mockResolvedValue(makeConnection({ provider: 'gcp_cloud_run', status: 'pending_validation', setup: { ...FEDERATED, federation_issuer: null } }));
    renderApp('/providers/conn-1', api);
    expect(await screen.findByText('Setup steps')).toBeInTheDocument();
    expect(screen.queryByText(/cloudbridge-connect\.py/)).not.toBeInTheDocument();
  });

  it('fills in a new Azure connection from the link the script prints, and offers the script first', async () => {
    const api = createFakeApi();
    renderApp(
      '/providers/new?provider=azure_container_apps&default_region=eastus&tenant_id=11111111-1111-1111-1111-111111111111' +
        '&subscription_id=22222222-2222-2222-2222-222222222222&client_id=33333333-3333-3333-3333-333333333333' +
        '&resource_group=apps-rg&environment_name=apps-env&registry_name=acmeregistry',
      api,
    );

    expect(await screen.findByLabelText(/Application \(client\) ID/)).toHaveValue('33333333-3333-3333-3333-333333333333');
    expect(screen.getByLabelText(/Tenant ID/)).toHaveValue('11111111-1111-1111-1111-111111111111');
    expect(screen.getByLabelText(/Resource group/)).toHaveValue('apps-rg');
    expect(screen.getByLabelText(/Default region/)).toHaveValue('eastus');
    await waitFor(() => expect(api.providers.getSetup).toHaveBeenCalledWith('azure_container_apps', expect.anything()));
    const command = await screen.findByText(/^python3 cloudbridge-connect\.py azure /);
    expect(command).toHaveTextContent('--resource-group apps-rg --environment apps-env --location eastus --registry acmeregistry');
    expect(command).toHaveTextContent(`--issuer ${ISSUER} --subject ${SUBJECT}`);
    expect(command).toHaveTextContent(/--cloudbridge-url http:\/\/localhost(:\d+)?$/);
    expect(screen.getByRole('link', { name: 'Azure Cloud Shell' })).toHaveAttribute('href', 'https://shell.azure.com');
  });

  it('asks for the region before giving the Azure command, and explains when Azure sign-in is not configured', async () => {
    const api = createFakeApi();
    const { user, unmount } = renderApp('/providers/new?provider=azure_container_apps', api);
    expect(await screen.findByText('Choose the default region below to get the command.')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Default region/), 'eastus2');
    expect(await screen.findByText(/^python3 .*--location eastus2/)).toBeInTheDocument();
    unmount();

    api.providers.getSetup.mockResolvedValue({ ...FEDERATED, federation_issuer: null });
    renderApp('/providers/new?provider=azure_container_apps', api);
    expect(await screen.findByText(/not set up to sign in to Azure yet/)).toBeInTheDocument();
  });

  it('does not offer the new-connection script for AWS or OIDC clusters', async () => {
    const api = createFakeApi();
    const { user, unmount } = renderApp('/providers/new?provider=kubernetes', api);
    expect(await screen.findByText(/^python3 cloudbridge-connect\.py aks /)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/How CloudBridge signs in/), 'oidc');
    expect(screen.queryByText(/^python3 cloudbridge-connect\.py/)).not.toBeInTheDocument();
    unmount();

    renderApp('/providers/new?provider=aws_app_runner', api);
    expect(await screen.findByLabelText(/IAM role ARN/)).toBeInTheDocument();
    expect(screen.queryByText(/^python3 cloudbridge-connect\.py/)).not.toBeInTheDocument();
    expect(api.providers.getSetup).toHaveBeenCalledTimes(1);
  });
});
