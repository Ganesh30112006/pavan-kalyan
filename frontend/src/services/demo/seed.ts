import type {
  Application,
  ApplicationConfiguration,
  AuditEvent,
  Deployment,
  DeploymentStage,
  NotificationPreferences,
  OrganizationMember,
  ProviderConnection,
  Session,
} from '../../types/api';
import { simulateEffective } from './capabilities';

/**
 * Sample records for development demo mode. Names and identifiers are
 * deliberately fictitious (".invalid" hosts, zeroed account numbers) so they
 * cannot be mistaken for real cloud resources.
 */

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

export function stagesAllSucceeded(start: string, end: string): DeploymentStage[] {
  return (['queued', 'validation', 'build', 'publishing', 'provisioning', 'verification'] as const).map((name) => ({
    name,
    status: 'succeeded',
    started_at: start,
    finished_at: end,
  }));
}

export interface DemoState {
  session: Session;
  connections: ProviderConnection[];
  applications: Application[];
  configurations: Record<string, ApplicationConfiguration>;
  deployments: Deployment[];
  audit: AuditEvent[];
  members: OrganizationMember[];
  notifications: NotificationPreferences;
  organizationCreatedAt: string;
}

export function createSeed(): DemoState {
  const session: Session = {
    user: {
      id: 'demo-user-1',
      email: 'demo.user@example.com',
      display_name: 'Demo User',
      role: 'admin',
      preferences: { timezone: null, time_format: '24h' },
    },
    organization: { id: 'demo-org', name: 'Demo organization' },
    permissions: [
      'applications:create',
      'applications:update',
      'applications:delete',
      'deployments:create',
      'deployments:cancel',
      'deployments:rollback',
      'providers:manage',
      'audit:read',
      'organization:manage',
    ],
    features: { organization_settings: true, notification_preferences: true },
  };

  const connections: ProviderConnection[] = [
    {
      id: 'demo-conn-aws',
      name: 'Demo AWS account',
      provider: 'aws_app_runner',
      status: 'connected',
      default_region: 'eu-west-1',
      config: {
        account_id: '000000000000',
        role_arn: 'arn:aws:iam::000000000000:role/CloudBridgeDemo',
        project_id: null,
        workload_identity_provider: null,
        service_account_email: null,
        tenant_id: null,
        subscription_id: null,
        client_id: null,
        resource_group: null,
        environment_name: null,
        registry_name: null,
        ecs_cluster: null,
        api_server: null,
        namespace: null,
        kubernetes_auth: null,
        token_audience: null,
      },
      setup: {
        external_id: 'demo-external-id',
        trusted_principal: 'arn:aws:iam::000000000000:root',
        federation_issuer: null,
        federation_audience: null,
        aws: {
          ecr_repository_prefix: 'cloudbridge',
          apprunner_access_role: 'CloudBridgeAppRunnerECRAccess',
          apprunner_instance_role: 'CloudBridgeAppRunnerInstance',
          codebuild_role: 'CloudBridgeCodeBuild',
          ecs_execution_role: 'CloudBridgeECSExecution',
          ecs_infrastructure_role: 'CloudBridgeECSInfrastructure',
        },
      },
      validation: {
        stage: null,
        started_at: minutesAgo(3000),
        finished_at: minutesAgo(2999),
        checks: [
          { name: 'Assume role', status: 'passed', detail: null },
          { name: 'App Runner permissions', status: 'passed', detail: null },
          { name: 'ECR read access', status: 'passed', detail: null },
        ],
        error_code: null,
      },
      created_at: minutesAgo(4000),
      last_validated_at: minutesAgo(2999),
    },
    {
      id: 'demo-conn-gcp',
      name: 'Demo Google Cloud project',
      provider: 'gcp_cloud_run',
      status: 'insufficient_permissions',
      default_region: 'europe-west1',
      config: {
        account_id: null,
        role_arn: null,
        project_id: 'demo-project-000',
        workload_identity_provider:
          'projects/000000000000/locations/global/workloadIdentityPools/demo-pool/providers/demo-provider',
        service_account_email: 'cloudbridge@demo-project-000.iam.gserviceaccount.com',
        tenant_id: null,
        subscription_id: null,
        client_id: null,
        resource_group: null,
        environment_name: null,
        registry_name: null,
        ecs_cluster: null,
        api_server: null,
        namespace: null,
        kubernetes_auth: null,
        token_audience: null,
      },
      setup: { external_id: null, trusted_principal: 'demo-principal', federation_issuer: null, federation_audience: null, aws: null },
      validation: {
        stage: null,
        started_at: minutesAgo(600),
        finished_at: minutesAgo(599),
        checks: [
          { name: 'Exchange workload identity token', status: 'passed', detail: null },
          { name: 'Cloud Run Admin (run.services.create)', status: 'failed', detail: 'Permission not granted to the service account.' },
          { name: 'Artifact Registry read access', status: 'skipped', detail: null },
        ],
        error_code: 'insufficient_permissions',
      },
      created_at: minutesAgo(700),
      last_validated_at: minutesAgo(599),
    },
  ];

  const demoDigest = (seed: string) => `sha256:${seed.repeat(64).slice(0, 64)}`;

  const applications: Application[] = [
    {
      id: 'demo-app-orders',
      name: 'orders-api',
      description: 'Sample order service (demo data).',
      application_type: 'api',
      runtime: 'node',
      provider: 'aws_app_runner',
      connection_id: 'demo-conn-aws',
      region: 'eu-west-1',
      status: 'running',
      health: 'healthy',
      current_version: '1.4.2',
      latest_deployment: { id: 'demo-dep-3', status: 'succeeded', created_at: minutesAgo(90) },
      updated_at: minutesAgo(85),
      source: {
        type: 'image',
        image: '000000000000.dkr.ecr.eu-west-1.amazonaws.com/orders-api:1.4.2',
        repository_url: null,
        ref_type: null,
        git_ref: null,
        dockerfile_path: null,
        build_context: null,
      },
      image_digest: demoDigest('a1'),
      service_url: 'https://orders-api.demo.invalid',
      created_at: minutesAgo(20000),
      created_by: 'Demo User',
      environment: 'production',
      promote_to_id: null,
      preview_of_id: null,
      preview_number: null,
      paused: false,
      requires_approval: false,
      auto_deploy: false,
    },
    {
      id: 'demo-app-billing',
      name: 'billing-api',
      description: 'Sample billing service with intermittent health-check failures (demo data).',
      application_type: 'api',
      runtime: 'python',
      provider: 'aws_app_runner',
      connection_id: 'demo-conn-aws',
      region: 'eu-west-1',
      status: 'running',
      health: 'degraded',
      current_version: '2.0.1',
      latest_deployment: { id: 'demo-dep-h1', status: 'succeeded', created_at: minutesAgo(1500) },
      updated_at: minutesAgo(40),
      source: {
        type: 'repository',
        image: null,
        repository_url: 'https://git.example.com/demo/billing-api.git',
        ref_type: 'commit',
        git_ref: '9b1c0de4f2a7e3b5c8d6a0f1e2b3c4d5e6f7a8b9',
        dockerfile_path: 'Dockerfile',
        build_context: '.',
      },
      image_digest: demoDigest('b2'),
      service_url: 'https://billing-api.demo.invalid',
      created_at: minutesAgo(18000),
      created_by: 'Sample Developer',
      environment: 'production',
      promote_to_id: null,
      preview_of_id: null,
      preview_number: null,
      paused: false,
      requires_approval: false,
      auto_deploy: false,
    },
    {
      id: 'demo-app-web',
      name: 'storefront-web',
      description: null,
      application_type: 'web_service',
      runtime: 'node',
      provider: 'aws_app_runner',
      connection_id: 'demo-conn-aws',
      region: 'eu-west-1',
      status: 'failed',
      health: 'unhealthy',
      current_version: null,
      latest_deployment: { id: 'demo-dep-2', status: 'failed', created_at: minutesAgo(300) },
      updated_at: minutesAgo(295),
      source: {
        type: 'repository',
        image: null,
        repository_url: 'https://git.example.com/demo/storefront-web.git',
        ref_type: 'branch',
        git_ref: 'main',
        dockerfile_path: 'Dockerfile',
        build_context: '.',
      },
      image_digest: null,
      service_url: null,
      created_at: minutesAgo(4500),
      created_by: 'Demo User',
      environment: 'production',
      promote_to_id: null,
      preview_of_id: null,
      preview_number: null,
      paused: false,
      requires_approval: false,
      auto_deploy: false,
    },
  ];

  const baseConfig = {
    port: 8080,
    cpu: '1',
    memory: '2Gi',
    min_instances: 1,
    max_instances: 4,
    concurrency: null,
    exposure: 'public' as const,
    health_check_path: '/healthz',
  };

  const ordersRequested = {
    ...baseConfig,
    min_instances: 0,
    concurrency: 80,
    environment: [
      { name: 'LOG_LEVEL', value: 'info' },
      { name: 'FEATURE_RETURNS', value: 'true' },
    ],
    secret_refs: [
      {
        name: 'DATABASE_URL',
        reference: 'arn:aws:secretsmanager:eu-west-1:000000000000:secret:demo/orders/database-url',
      },
    ],
  };
  const billingRequested = {
    ...baseConfig,
    cpu: '2',
    memory: '4Gi',
    environment: [{ name: 'LOG_LEVEL', value: 'warning' }],
    secret_refs: [],
  };

  const configurations: Record<string, ApplicationConfiguration> = {
    'demo-app-orders': {
      application_id: 'demo-app-orders',
      version: 3,
      ...ordersRequested,
      updated_at: minutesAgo(100),
      ...simulateEffective(ordersRequested, 'aws_app_runner', minutesAgo(85)),
    },
    'demo-app-billing': {
      application_id: 'demo-app-billing',
      version: 2,
      ...billingRequested,
      updated_at: minutesAgo(1600),
      ...simulateEffective(billingRequested, 'aws_app_runner', minutesAgo(1495)),
    },
    'demo-app-web': {
      application_id: 'demo-app-web',
      version: 1,
      ...baseConfig,
      port: 3000,
      environment: [{ name: 'NODE_ENV', value: 'production' }],
      secret_refs: [],
      updated_at: minutesAgo(4500),
      effective: null,
      adjustments: [],
    },
  };

  const base = {
    region: 'eu-west-1',
    provider: 'aws_app_runner' as const,
    initiated_by: 'Demo User',
    rollback_of: null,
    failure: null,
    progress_percent: null,
    allowed_actions: { rollback: false, cancel: false, approve: false },
    trigger: 'manual' as const,
    approved_by: null,
    approved_at: null,
  };

  /** A finished deployment `ago` minutes in the past that took `took` minutes. */
  function finished(
    id: string,
    app: Application,
    ago: number,
    took: number,
    status: 'succeeded' | 'failed' | 'rolled_back',
    version: string,
    digestSeed: string,
  ): Deployment {
    const start = minutesAgo(ago);
    const end = minutesAgo(ago - took);
    const failedAt = status === 'failed' ? 'build' : status === 'rolled_back' ? 'verification' : null;
    const stages: DeploymentStage[] = failedAt
      ? (['queued', 'validation', 'build', 'publishing', 'provisioning', 'verification'] as const).map((name, i, all) => {
          const failIndex = all.indexOf(failedAt);
          if (i < failIndex) return { name, status: 'succeeded', started_at: start, finished_at: start };
          if (i === failIndex) return { name, status: 'failed', started_at: start, finished_at: end };
          return { name, status: 'skipped', started_at: null, finished_at: null };
        })
      : stagesAllSucceeded(start, end);
    return {
      ...base,
      id,
      application_id: app.id,
      application_name: app.name,
      status,
      kind: 'deploy',
      version,
      image_digest: status === 'failed' ? null : demoDigest(digestSeed),
      source_ref: app.source.type === 'image' ? `${app.name}:${version}` : `${app.source.git_ref?.slice(0, 7) ?? 'main'}`,
      created_at: start,
      started_at: start,
      finished_at: end,
      stages,
      failure:
        status === 'failed'
          ? { code: 'build_failed', message: 'Simulated: the container build failed (npm ci exited with code 1).' }
          : status === 'rolled_back'
            ? { code: 'verification_failed', message: 'Simulated: health checks failed; the previous revision was restored.' }
            : null,
      service_url: status === 'succeeded' ? `https://${app.name}.demo.invalid` : null,
    };
  }

  const [orders, billing, web] = applications as [Application, Application, Application];
  const day = 24 * 60;
  // Recent history across several days, so the overview trend chart has real (simulated) records to draw.
  const deployments: Deployment[] = [
    finished('demo-dep-h6', orders, 9 * day, 6, 'succeeded', '1.3.0', 'c3'),
    finished('demo-dep-h5', billing, 8 * day, 7, 'failed', '1.9.0', 'd4'),
    finished('demo-dep-h4', billing, 8 * day - 60, 8, 'succeeded', '1.9.1', 'e5'),
    finished('demo-dep-h3', orders, 6 * day, 5, 'succeeded', '1.4.0', 'f6'),
    finished('demo-dep-h2', billing, 4 * day, 9, 'rolled_back', '2.0.0', '07'),
    finished('demo-dep-1', orders, 2000, 6, 'succeeded', '1.4.1', '18'),
    finished('demo-dep-h1', billing, 1500, 7, 'succeeded', '2.0.1', 'b2'),
    finished('demo-dep-2', web, 300, 4, 'failed', '0.1.0', '29'),
    finished('demo-dep-3', orders, 90, 5, 'succeeded', '1.4.2', 'a1'),
  ];

  const audit: AuditEvent[] = [
    {
      id: 'demo-audit-1',
      occurred_at: minutesAgo(90),
      actor: { id: 'demo-user-1', display_name: 'Demo User', email: 'demo.user@example.com' },
      action: 'deployment.created',
      resource_type: 'deployment',
      resource_id: 'demo-dep-3',
      resource_name: 'orders-api',
      outcome: 'success',
      request_id: 'demo-req-0003',
    },
    {
      id: 'demo-audit-2',
      occurred_at: minutesAgo(300),
      actor: { id: 'demo-user-1', display_name: 'Demo User', email: 'demo.user@example.com' },
      action: 'deployment.created',
      resource_type: 'deployment',
      resource_id: 'demo-dep-2',
      resource_name: 'storefront-web',
      outcome: 'success',
      request_id: 'demo-req-0002',
    },
    {
      id: 'demo-audit-3',
      occurred_at: minutesAgo(599),
      actor: null,
      action: 'provider_connection.validated',
      resource_type: 'provider_connection',
      resource_id: 'demo-conn-gcp',
      resource_name: 'Demo Google Cloud project',
      outcome: 'failure',
      request_id: 'demo-req-0001',
    },
  ];

  const members: OrganizationMember[] = [
    {
      id: 'demo-user-1',
      display_name: 'Demo User',
      email: 'demo.user@example.com',
      role: 'admin',
      joined_at: minutesAgo(9000),
    },
    {
      id: 'demo-user-2',
      display_name: 'Sample Developer',
      email: 'sample.developer@example.com',
      role: 'developer',
      joined_at: minutesAgo(8000),
    },
  ];

  return {
    session,
    connections,
    applications,
    configurations,
    deployments,
    audit,
    members,
    notifications: {
      email_enabled: true,
      events: {
        deployment_succeeded: false,
        deployment_failed: true,
        rollback_completed: true,
        provider_connection_failed: true,
        application_deleted: false,
        application_unhealthy: true,
        approval_requested: true,
      },
    },
    organizationCreatedAt: minutesAgo(9000),
  };
}
