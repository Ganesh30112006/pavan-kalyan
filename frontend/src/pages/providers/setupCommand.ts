import type { AwsSetup, ConnectionSetup, ProviderConnection, ProviderKind } from '../../types/api';

/** The guided setup script, served from public/ next to the app. Customers run it in their own cloud shell. */
export const SETUP_SCRIPT = 'cloudbridge-connect.py';
export const AZURE_AUDIENCE = 'api://AzureADTokenExchange';

/** The script's own defaults; a flag is only added when CloudBridge is configured differently. */
const SCRIPT_DEFAULTS: AwsSetup = {
  ecr_repository_prefix: 'cloudbridge',
  apprunner_access_role: 'CloudBridgeAppRunnerECRAccess',
  apprunner_instance_role: 'CloudBridgeAppRunnerInstance',
  codebuild_role: 'CloudBridgeCodeBuild',
  ecs_execution_role: 'CloudBridgeECSExecution',
  ecs_infrastructure_role: 'CloudBridgeECSInfrastructure',
};

const AWS_FLAGS: Record<keyof AwsSetup, string> = {
  ecr_repository_prefix: '--ecr-prefix',
  apprunner_access_role: '--apprunner-access-role',
  apprunner_instance_role: '--apprunner-instance-role',
  codebuild_role: '--codebuild-role',
  ecs_execution_role: '--ecs-execution-role',
  ecs_infrastructure_role: '--ecs-infrastructure-role',
};

type Option = [flag: string, value: string | null | undefined | boolean];

export function scriptUrl(origin: string, base: string): string {
  return `${origin}${base.endsWith('/') ? base : `${base}/`}${SETUP_SCRIPT}`;
}

/** Quotes a value for a POSIX shell. IDs, ARNs, names and URLs pass through unchanged. */
export function shellArg(value: string): string {
  return /^[A-Za-z0-9_./:=@,+%-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}

function command(subcommand: string, options: Option[]): string {
  const parts = ['python3', SETUP_SCRIPT, subcommand];
  for (const [flag, value] of options) {
    if (value === true) parts.push(flag);
    else if (typeof value === 'string' && value.trim()) parts.push(flag, shellArg(value.trim()));
  }
  return parts.join(' ');
}

function awsRoleOptions(aws: AwsSetup | null, ecs: boolean): Option[] {
  if (!aws) return [];
  const keys: Array<keyof AwsSetup> = [
    'ecr_repository_prefix',
    'codebuild_role',
    ...(ecs ? (['ecs_execution_role', 'ecs_infrastructure_role'] as const) : (['apprunner_access_role', 'apprunner_instance_role'] as const)),
  ];
  return keys.filter((key) => aws[key] !== SCRIPT_DEFAULTS[key]).map((key): Option => [AWS_FLAGS[key], aws[key]]);
}

function audienceOption(audience: string | null): Option[] {
  return audience && audience !== AZURE_AUDIENCE ? [['--audience', audience]] : [];
}

/** The command that sets up an existing connection's account, or null when the script does not cover it. */
export function connectionCommand(c: ProviderConnection): string | null {
  const setup = c.setup;
  if (!setup?.trusted_principal) return null;
  const { config } = c;
  if (c.provider === 'aws_app_runner' || c.provider === 'aws_ecs_express') {
    if (!config.role_arn || !setup.external_id) return null;
    const ecs = c.provider === 'aws_ecs_express';
    return command('aws', [
      ['--role-arn', config.role_arn],
      ['--principal', setup.trusted_principal],
      ['--external-id', setup.external_id],
      ['--account', config.account_id],
      ['--ecs', ecs],
      ...awsRoleOptions(setup.aws, ecs),
    ]);
  }
  if (!setup.federation_issuer) return null;
  if (c.provider === 'azure_container_apps') {
    if (!config.client_id || !config.resource_group || !config.environment_name) return null;
    return command('azure', [
      ['--client-id', config.client_id],
      ['--tenant', config.tenant_id],
      ['--subscription', config.subscription_id],
      ['--resource-group', config.resource_group],
      ['--environment', config.environment_name],
      ['--location', c.default_region],
      ['--registry', config.registry_name],
      ['--issuer', setup.federation_issuer],
      ['--subject', setup.trusted_principal],
      ...audienceOption(setup.federation_audience),
    ]);
  }
  if (c.provider === 'kubernetes' && config.namespace) {
    if (config.kubernetes_auth === 'aks') {
      if (!config.client_id || !config.api_server) return null;
      return command('aks', [
        ['--api-server', config.api_server],
        ['--namespace', config.namespace],
        ['--client-id', config.client_id],
        ['--tenant', config.tenant_id],
        ['--issuer', setup.federation_issuer],
        ['--subject', setup.trusted_principal],
        ...audienceOption(setup.federation_audience),
      ]);
    }
    if (!setup.federation_audience) return null;
    return command('kubernetes', [
      ['--namespace', config.namespace],
      ['--issuer', setup.federation_issuer],
      ['--subject', setup.trusted_principal],
      ['--audience', setup.federation_audience],
    ]);
  }
  return null;
}

export interface NewConnectionValues {
  default_region: string;
  tenant_id: string;
  subscription_id: string;
  resource_group: string;
  environment_name: string;
  registry_name: string;
  api_server: string;
  namespace: string;
}

/** Names the script uses when the form leaves them empty; the link it prints fills them in. */
export const NEW_CONNECTION_DEFAULTS = { resource_group: 'cloudbridge-apps', environment_name: 'cloudbridge-env', namespace: 'cloudbridge' };

/**
 * Before a connection exists (Azure and AKS: the app registration's client ID is not known yet), a command that
 * creates everything from what the form holds so far and prints a link that fills the form in.
 */
export function newConnectionCommand(
  provider: ProviderKind,
  setup: ConnectionSetup,
  values: NewConnectionValues,
  cloudbridgeUrl: string,
): string | null {
  if (!setup.federation_issuer || !setup.trusted_principal) return null;
  const federation: Option[] = [
    ['--tenant', values.tenant_id],
    ['--subscription', values.subscription_id],
    ['--issuer', setup.federation_issuer],
    ['--subject', setup.trusted_principal],
    ...audienceOption(setup.federation_audience),
    ['--cloudbridge-url', cloudbridgeUrl],
  ];
  if (provider === 'azure_container_apps') {
    if (!values.default_region) return null;
    return command('azure', [
      ['--resource-group', values.resource_group || NEW_CONNECTION_DEFAULTS.resource_group],
      ['--environment', values.environment_name || NEW_CONNECTION_DEFAULTS.environment_name],
      ['--location', values.default_region],
      ['--registry', values.registry_name],
      ...federation,
    ]);
  }
  if (provider === 'kubernetes') {
    const cluster: Option[] = values.api_server
      ? [['--api-server', values.api_server]]
      : [
          ['--cluster', 'CLUSTER_NAME'],
          ['--cluster-resource-group', 'CLUSTER_RESOURCE_GROUP'],
        ];
    return command('aks', [...cluster, ['--namespace', values.namespace || NEW_CONNECTION_DEFAULTS.namespace], ...federation]);
  }
  return null;
}
