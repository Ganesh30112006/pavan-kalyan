import type {
  ConfigAdjustment,
  EffectiveConfiguration,
  ProviderCapabilities,
  ProviderKind,
  RequestedConfiguration,
} from '../../types/api';
import { PROVIDERS } from '../../utils/providers';

/**
 * Simulated answers to GET /providers/{provider}/capabilities for demo mode.
 * In live mode these come from the backend; the UI never relies on this file.
 */
export const DEMO_CAPABILITIES: Record<ProviderKind, ProviderCapabilities> = {
  aws_app_runner: {
    provider: 'aws_app_runner',
    regions: [...PROVIDERS.aws_app_runner.regions],
    cpu_options: ['0.25', '0.5', '1', '2', '4'],
    memory_options: ['512Mi', '1Gi', '2Gi', '3Gi', '4Gi', '6Gi', '8Gi', '10Gi', '12Gi'],
    min_instances_floor: 1,
    max_instances_limit: 25,
    supports_concurrency: true,
    max_concurrency: 200,
    supports_private_exposure: true,
    supported_application_types: ['web_service', 'api'],
    supports_secret_references: true,
    notes: [
      'Services always keep at least one provisioned instance.',
      'Private services are reachable through a VPC interface endpoint.',
    ],
    features: { repository_builds: true, metrics: true, custom_domains: true, pause: true },
  },
  gcp_cloud_run: {
    provider: 'gcp_cloud_run',
    regions: [...PROVIDERS.gcp_cloud_run.regions],
    cpu_options: ['1', '2', '4', '8'],
    memory_options: ['512Mi', '1Gi', '2Gi', '4Gi', '8Gi', '16Gi', '32Gi'],
    min_instances_floor: 0,
    max_instances_limit: 100,
    supports_concurrency: true,
    max_concurrency: 1000,
    supports_private_exposure: true,
    supported_application_types: ['web_service', 'api', 'worker'],
    supports_secret_references: true,
    notes: ['Services can scale to zero when idle.', 'Private services use internal ingress.'],
    features: { repository_builds: true, metrics: false, custom_domains: false, pause: false },
  },
  azure_container_apps: {
    provider: 'azure_container_apps',
    regions: [...PROVIDERS.azure_container_apps.regions],
    cpu_options: ['0.25', '0.5', '0.75', '1', '1.25', '1.5', '1.75', '2'],
    memory_options: ['512Mi', '1Gi', '1536Mi', '2Gi', '2560Mi', '3Gi', '3584Mi', '4Gi'],
    min_instances_floor: 0,
    max_instances_limit: 300,
    supports_concurrency: true,
    max_concurrency: 1000,
    supports_private_exposure: true,
    supported_application_types: ['web_service', 'api'],
    supports_secret_references: true,
    notes: ['Apps can scale to zero when idle.', 'CPU and memory come in fixed pairs of 1 vCPU to 2Gi.'],
    features: { repository_builds: true, metrics: true, custom_domains: true, pause: true },
  },
  aws_ecs_express: {
    provider: 'aws_ecs_express',
    regions: [...PROVIDERS.aws_ecs_express.regions],
    cpu_options: ['0.25', '0.5', '1', '2', '4'],
    memory_options: ['512Mi', '1Gi', '2Gi', '3Gi', '4Gi', '8Gi', '16Gi'],
    min_instances_floor: 1,
    max_instances_limit: 100,
    supports_concurrency: false,
    max_concurrency: null,
    supports_private_exposure: false,
    supported_application_types: ['web_service', 'api'],
    supports_secret_references: true,
    notes: ['Runs on Fargate behind a managed load balancer.', 'Tasks scale on average CPU.'],
    features: { repository_builds: true, metrics: true, custom_domains: false, pause: true },
  },
  kubernetes: {
    provider: 'kubernetes',
    regions: ['in-cluster'],
    cpu_options: ['0.25', '0.5', '1', '2', '4'],
    memory_options: ['256Mi', '512Mi', '1Gi', '2Gi', '4Gi', '8Gi'],
    min_instances_floor: 1,
    max_instances_limit: 100,
    supports_concurrency: false,
    max_concurrency: null,
    supports_private_exposure: true,
    supported_application_types: ['web_service', 'api', 'worker'],
    supports_secret_references: true,
    notes: ['Creates a Deployment, Service and HorizontalPodAutoscaler.', 'Deploys images only.'],
    features: { repository_builds: false, metrics: true, custom_domains: false, pause: true },
  },
};

/** What the simulated provider would actually run for a requested spec. */
export function simulateEffective(
  requested: RequestedConfiguration,
  provider: ProviderKind,
  observedAt: string,
): { effective: EffectiveConfiguration; adjustments: ConfigAdjustment[] } {
  const caps = DEMO_CAPABILITIES[provider];
  const adjustments: ConfigAdjustment[] = [];
  const minInstances = Math.max(requested.min_instances, caps.min_instances_floor);
  if (minInstances !== requested.min_instances) {
    adjustments.push({
      field: 'min_instances',
      requested: String(requested.min_instances),
      effective: String(minInstances),
      reason: `Simulated: ${PROVIDERS[provider].label} keeps at least ${caps.min_instances_floor} instance running.`,
    });
  }
  const concurrency = caps.supports_concurrency ? requested.concurrency : null;
  if (concurrency !== requested.concurrency) {
    adjustments.push({
      field: 'concurrency',
      requested: String(requested.concurrency),
      effective: 'provider default',
      reason: 'Simulated: the provider does not support a concurrency setting.',
    });
  }
  return {
    effective: {
      port: requested.port,
      cpu: requested.cpu,
      memory: requested.memory,
      min_instances: minInstances,
      max_instances: requested.max_instances,
      concurrency: concurrency ?? (provider === 'aws_app_runner' ? 100 : 80),
      exposure: requested.exposure,
      health_check_path: requested.health_check_path,
      observed_at: observedAt,
    },
    adjustments,
  };
}
