import type {
  AlertChannel,
  ApiToken,
  Automation,
  CostEstimate,
  CustomDomain,
  MetricRange,
  Metrics,
  Application,
  ApplicationConfiguration,
  ApplicationSummary,
  AuditEvent,
  Deployment,
  Paginated,
  Permission,
  ProviderCapabilities,
  ProviderConnection,
  Session,
} from '../../src/types/api';

export const ALL_PERMISSIONS: Permission[] = [
  'applications:create',
  'applications:update',
  'applications:delete',
  'deployments:create',
  'deployments:cancel',
  'deployments:approve',
  'deployments:rollback',
  'providers:manage',
  'audit:read',
  'organization:manage',
];

export function makeSession(permissions: Permission[] = ALL_PERMISSIONS, overrides: Partial<Session> = {}): Session {
  return {
    user: {
      id: 'u1',
      email: 'ada@example.com',
      display_name: 'Ada Admin',
      role: 'admin',
      preferences: { timezone: 'UTC', time_format: '24h' },
    },
    organization: { id: 'o1', name: 'Acme' },
    permissions,
    features: { organization_settings: true, notification_preferences: true },
    ...overrides,
  };
}

const T = '2026-10-01T10:00:00Z';

export function makeAppSummary(overrides: Partial<ApplicationSummary> = {}): ApplicationSummary {
  return {
    id: 'app-1',
    name: 'orders-api',
    description: 'Order service',
    application_type: 'api',
    runtime: 'node',
    provider: 'aws_app_runner',
    connection_id: 'conn-1',
    region: 'eu-west-1',
    status: 'running',
    health: 'healthy',
    current_version: '1.0.0',
    latest_deployment: { id: 'dep-1', status: 'succeeded', created_at: T },
    updated_at: T,
    ...overrides,
  };
}

export function makeApp(overrides: Partial<Application> = {}): Application {
  return {
    ...makeAppSummary(),
    source: {
      type: 'image',
      image: 'registry.example.com/orders:1.0.0',
      repository_url: null,
      ref_type: null,
      git_ref: null,
      dockerfile_path: null,
      build_context: null,
    },
    image_digest: `sha256:${'a'.repeat(64)}`,
    service_url: 'https://orders.example.com',
    created_at: T,
    created_by: 'Ada Admin',
    environment: 'production',
    promote_to_id: null,
    preview_of_id: null,
    preview_number: null,
    paused: false,
    requires_approval: false,
    auto_deploy: false,
    ...overrides,
  };
}

export function page<T>(items: T[], total = items.length, pageNo = 1, pageSize = 25): Paginated<T> {
  return { items, total, page: pageNo, page_size: pageSize };
}

export function makeConfig(overrides: Partial<ApplicationConfiguration> = {}): ApplicationConfiguration {
  return {
    application_id: 'app-1',
    version: 3,
    port: 8080,
    cpu: '1',
    memory: '2Gi',
    min_instances: 1,
    max_instances: 3,
    concurrency: null,
    exposure: 'public',
    health_check_path: '/healthz',
    environment: [{ name: 'LOG_LEVEL', value: 'info' }],
    secret_refs: [{ name: 'DATABASE_URL', reference: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:orders/db' }],
    updated_at: T,
    effective: null,
    adjustments: [],
    ...overrides,
  };
}

export function makeDeployment(overrides: Partial<Deployment> = {}): Deployment {
  return {
    id: 'dep-12345678abcd',
    application_id: 'app-1',
    application_name: 'orders-api',
    provider: 'aws_app_runner',
    region: 'eu-west-1',
    status: 'building',
    kind: 'deploy',
    version: '1.0.0',
    image_digest: null,
    source_ref: 'orders:1.0.0',
    initiated_by: 'Ada Admin',
    created_at: T,
    finished_at: null,
    rollback_of: null,
    started_at: T,
    stages: [
      { name: 'queued', status: 'succeeded', started_at: T, finished_at: T },
      { name: 'validation', status: 'succeeded', started_at: T, finished_at: T },
      { name: 'build', status: 'active', started_at: T, finished_at: null },
      { name: 'publishing', status: 'pending', started_at: null, finished_at: null },
      { name: 'provisioning', status: 'pending', started_at: null, finished_at: null },
      { name: 'verification', status: 'pending', started_at: null, finished_at: null },
    ],
    failure: null,
    service_url: null,
    progress_percent: null,
    allowed_actions: { rollback: false, cancel: false, approve: false },
    trigger: 'manual',
    approved_by: null,
    approved_at: null,
    ...overrides,
  };
}

export function makeCapabilities(overrides: Partial<ProviderCapabilities> = {}): ProviderCapabilities {
  return {
    provider: 'aws_app_runner',
    regions: ['eu-west-1', 'us-east-1'],
    cpu_options: ['0.25', '0.5', '1', '2', '4'],
    memory_options: ['512Mi', '1Gi', '2Gi', '4Gi'],
    min_instances_floor: 1,
    max_instances_limit: 25,
    supports_concurrency: true,
    max_concurrency: 200,
    supports_private_exposure: true,
    supported_application_types: ['web_service', 'api'],
    supports_secret_references: true,
    notes: [],
    features: { repository_builds: true, metrics: true, custom_domains: true, pause: true },
    ...overrides,
  };
}

export function makeConnection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
  return {
    id: 'conn-1',
    name: 'Production AWS',
    provider: 'aws_app_runner',
    status: 'connected',
    default_region: 'eu-west-1',
    config: {
      account_id: '123456789012',
      role_arn: 'arn:aws:iam::123456789012:role/CloudBridge',
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
      external_id: 'ext-123',
      trusted_principal: 'arn:aws:iam::999999999999:role/cloudbridge',
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
    validation: null,
    created_at: T,
    last_validated_at: T,
    ...overrides,
  };
}

export function makeAuditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 'a1',
    occurred_at: T,
    actor: { id: 'u1', display_name: 'Ada Admin', email: 'ada@example.com' },
    action: 'deployment.created',
    resource_type: 'deployment',
    resource_id: 'dep-1',
    resource_name: 'orders-api',
    outcome: 'success',
    request_id: 'req-1',
    ...overrides,
  };
}

export function makeAutomation(overrides: Partial<Automation> = {}): Automation {
  return {
    environment: 'production',
    promote_to_id: null,
    auto_deploy: false,
    auto_deploy_branch: null,
    previews_enabled: false,
    config_from_repo: false,
    requires_approval: false,
    webhook: { url: 'https://cloudbridge.example.com/api/v1/hooks/github/app-1', secret_configured: false },
    ...overrides,
  };
}

export function makeMetrics(range: MetricRange = '1h'): Metrics {
  return {
    range,
    step_seconds: 60,
    series: [
      {
        name: 'requests',
        unit: 'count',
        points: [
          { timestamp: '2026-10-17T10:00:00Z', value: 12 },
          { timestamp: '2026-10-17T10:01:00Z', value: 30 },
        ],
      },
      { name: 'latency_ms', unit: 'ms', points: [{ timestamp: '2026-10-17T10:01:00Z', value: 84.5 }] },
    ],
    unavailable_reason: null,
  };
}

export function makeCost(overrides: Partial<CostEstimate> = {}): CostEstimate {
  return {
    provider: 'aws_app_runner',
    currency: 'USD',
    monthly_low: 10.22,
    monthly_high: 113.88,
    lines: [{ item: 'Instances', low: 10.22, high: 113.88 }],
    assumptions: ['Low: 1 instance provisioned but idle all month (memory only).'],
    ...overrides,
  };
}

export function makeDomain(overrides: Partial<CustomDomain> = {}): CustomDomain {
  return {
    id: 'dom-1',
    domain: 'api.example.com',
    status: 'pending_dns',
    records: [{ type: 'CNAME', name: 'api.example.com', value: 'abc.eu-west-1.awsapprunner.com', purpose: 'Routes traffic to App Runner' }],
    message: 'Create the DNS records below.',
    created_at: T,
    checked_at: T,
    ...overrides,
  };
}

export function makeAlertChannel(overrides: Partial<AlertChannel> = {}): AlertChannel {
  return {
    id: 'ch-1',
    name: 'Ops',
    kind: 'slack',
    host: 'hooks.slack.com',
    events: ['deployment_failed'],
    enabled: true,
    signed: false,
    created_at: T,
    last_delivery_at: null,
    last_delivery_status: null,
    ...overrides,
  };
}

export function makeApiToken(overrides: Partial<ApiToken> = {}): ApiToken {
  return { id: 'tok-1', name: 'CI', prefix: 'cbt_abcdefgh', created_at: T, last_used_at: null, expires_at: null, ...overrides };
}
