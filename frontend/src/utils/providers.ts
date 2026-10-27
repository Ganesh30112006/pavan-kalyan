import type { ProviderConnection, ProviderKind } from '../types/api';

export interface ProviderInfo {
  kind: ProviderKind;
  label: string;
  shortLabel: string;
  cloud: string;
  regions: readonly string[];
}

/**
 * Regions offered in forms. The backend validates the final value against the
 * provider, so this list only needs to be a sensible default set.
 */
export const PROVIDERS: Record<ProviderKind, ProviderInfo> = {
  aws_app_runner: {
    kind: 'aws_app_runner',
    label: 'AWS App Runner',
    shortLabel: 'App Runner',
    cloud: 'Amazon Web Services',
    regions: [
      'us-east-1',
      'us-east-2',
      'us-west-2',
      'ca-central-1',
      'eu-central-1',
      'eu-west-1',
      'eu-west-2',
      'eu-west-3',
      'ap-south-1',
      'ap-southeast-1',
      'ap-southeast-2',
      'ap-northeast-1',
    ],
  },
  gcp_cloud_run: {
    kind: 'gcp_cloud_run',
    label: 'Google Cloud Run',
    shortLabel: 'Cloud Run',
    cloud: 'Google Cloud',
    regions: [
      'us-central1',
      'us-east1',
      'us-east4',
      'us-west1',
      'northamerica-northeast1',
      'europe-west1',
      'europe-west2',
      'europe-west3',
      'europe-west4',
      'asia-south1',
      'asia-southeast1',
      'asia-northeast1',
      'australia-southeast1',
    ],
  },
  azure_container_apps: {
    kind: 'azure_container_apps',
    label: 'Azure Container Apps',
    shortLabel: 'Container Apps',
    cloud: 'Microsoft Azure',
    regions: [
      'eastus',
      'eastus2',
      'westus2',
      'westus3',
      'centralus',
      'canadacentral',
      'northeurope',
      'westeurope',
      'uksouth',
      'francecentral',
      'germanywestcentral',
      'centralindia',
      'southeastasia',
      'eastasia',
      'japaneast',
      'australiaeast',
      'uaenorth',
    ],
  },
  aws_ecs_express: {
    kind: 'aws_ecs_express',
    label: 'AWS ECS Express Mode',
    shortLabel: 'ECS Express',
    cloud: 'Amazon Web Services',
    regions: [
      'us-east-1',
      'us-east-2',
      'us-west-2',
      'ca-central-1',
      'eu-central-1',
      'eu-west-1',
      'eu-west-2',
      'eu-west-3',
      'ap-south-1',
      'ap-southeast-1',
      'ap-southeast-2',
      'ap-northeast-1',
    ],
  },
  kubernetes: {
    kind: 'kubernetes',
    label: 'Kubernetes',
    shortLabel: 'Kubernetes',
    cloud: 'Your cluster',
    regions: ['in-cluster'],
  },
};

export const PROVIDER_KINDS: readonly ProviderKind[] = [
  'aws_app_runner',
  'aws_ecs_express',
  'azure_container_apps',
  'gcp_cloud_run',
  'kubernetes',
];

export function providerLabel(kind: ProviderKind): string {
  return PROVIDERS[kind]?.label ?? kind;
}

export const CPU_OPTIONS = ['0.25', '0.5', '1', '2', '4'] as const;
export const MEMORY_OPTIONS = ['512Mi', '1Gi', '2Gi', '4Gi', '8Gi'] as const;

/** The AWS account ID, Google Cloud project ID or Azure subscription ID a connection targets. */
export function accountRef(c: ProviderConnection): string {
  if (c.provider === 'aws_app_runner' || c.provider === 'aws_ecs_express') return c.config.account_id ?? '—';
  if (c.provider === 'azure_container_apps') return c.config.subscription_id ?? '—';
  if (c.provider === 'kubernetes') return c.config.namespace ?? '—';
  return c.config.project_id ?? '—';
}

/** What the provider calls the scope a connection targets. */
export function accountLabel(kind: ProviderKind): string {
  if (kind === 'aws_app_runner' || kind === 'aws_ecs_express') return 'Account';
  if (kind === 'azure_container_apps') return 'Subscription';
  return kind === 'kubernetes' ? 'Namespace' : 'Project';
}
