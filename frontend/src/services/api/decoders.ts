import type {
  Application,
  ApplicationConfiguration,
  ApplicationSource,
  ApplicationStatus,
  ApplicationType,
  ConfigAdjustment,
  EffectiveConfiguration,
  HealthStatus,
  LogEntry,
  LogPage,
  ProviderCapabilities,
  RuntimeType,
  SpecValidationResult,
  AuditEvent,
  AuditOutcome,
  AwsSetup,
  CheckStatus,
  ConnectionConfig,
  ConnectionSetup,
  ConnectionStatus,
  ConnectionValidation,
  Deployment,
  DeploymentEvent,
  DeploymentRef,
  DeploymentStage,
  DeploymentStatus,
  DeploymentSummary,
  ApplicationSummary,
  NotificationEvent,
  NotificationPreferences,
  OperationAccepted,
  Organization,
  OrganizationMember,
  Paginated,
  Permission,
  ProviderConnection,
  ProviderKind,
  Session,
  StageName,
  StageStatus,
  User,
  UserRole,
  ValidationCheck,
  ValidationStage,
  ProviderFeatures,
  Automation,
  WebhookSecret,
  ScheduleState,
  Metrics,
  MetricName,
  CostEstimate,
  CustomDomain,
  AlertChannel,
  CreatedAlertChannel,
  ApiToken,
  CreatedApiToken,
} from '../../types/api';
import {
  array,
  boolean,
  dateTime,
  integer,
  nullable,
  number,
  object,
  oneOf,
  oneOfOr,
  oneOfOrNull,
  record,
  string,
  withDefault,
  type Decoder,
} from './decode';

export const PROVIDER_KINDS: readonly ProviderKind[] = [
  'aws_app_runner',
  'gcp_cloud_run',
  'azure_container_apps',
  'aws_ecs_express',
  'kubernetes',
];
const providerKind = oneOf(PROVIDER_KINDS);

export const APPLICATION_STATUSES: readonly ApplicationStatus[] = [
  'draft',
  'deploying',
  'running',
  'failed',
  'stopped',
  'deleting',
  'unknown',
];

export const DEPLOYMENT_STATUSES: readonly DeploymentStatus[] = [
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
  'unknown',
];

export const HEALTH_STATUSES: readonly HealthStatus[] = ['healthy', 'degraded', 'unhealthy', 'unknown'];

export const APPLICATION_TYPES: readonly ApplicationType[] = ['web_service', 'api', 'worker'];

export const RUNTIME_TYPES: readonly RuntimeType[] = ['node', 'python', 'java', 'go', 'dotnet', 'ruby', 'php', 'other'];

export const CONNECTION_STATUSES: readonly ConnectionStatus[] = [
  'pending_validation',
  'validating',
  'connected',
  'invalid_credentials',
  'insufficient_permissions',
  'provider_unavailable',
  'timed_out',
  'disconnected',
  'error',
  'unknown',
];

export const STAGE_NAMES: readonly StageName[] = [
  'queued',
  'validation',
  'build',
  'publishing',
  'provisioning',
  'verification',
];

const STAGE_STATUSES: readonly StageStatus[] = [
  'pending',
  'active',
  'succeeded',
  'failed',
  'cancelled',
  'skipped',
];

export const AUDIT_OUTCOMES: readonly AuditOutcome[] = ['success', 'failure', 'denied', 'unknown'];

const PERMISSIONS: readonly Permission[] = [
  'applications:create',
  'applications:update',
  'applications:delete',
  'deployments:create',
  'deployments:cancel',
  'deployments:rollback',
  'deployments:approve',
  'providers:manage',
  'audit:read',
  'organization:manage',
];

const USER_ROLES: readonly UserRole[] = ['owner', 'admin', 'developer', 'viewer'];

export const NOTIFICATION_EVENTS: readonly NotificationEvent[] = [
  'deployment_succeeded',
  'deployment_failed',
  'rollback_completed',
  'provider_connection_failed',
  'application_deleted',
  'application_unhealthy',
  'approval_requested',
];

/** Unknown permissions from a newer backend are ignored, never granted. */
const permissions: Decoder<Permission[]> = (value, path) =>
  array(string)(value, path).filter((p): p is Permission => PERMISSIONS.includes(p as Permission));

export function paginated<T>(item: Decoder<T>): Decoder<Paginated<T>> {
  return object<Paginated<T>>({
    items: array(item),
    total: integer,
    page: integer,
    page_size: integer,
  });
}

export const user = object<User>({
  id: string,
  email: string,
  display_name: string,
  role: oneOfOr(USER_ROLES, 'viewer'),
  preferences: withDefault(
    object<User['preferences']>({
      timezone: nullable(string),
      time_format: withDefault(oneOfOr(['12h', '24h'] as const, '24h'), '24h'),
    }),
    { timezone: null, time_format: '24h' },
  ),
});

export const session = object<Session>({
  user,
  organization: object<Session['organization']>({ id: string, name: string }),
  permissions,
  features: withDefault(
    object<Session['features']>({
      organization_settings: withDefault(boolean, false),
      notification_preferences: withDefault(boolean, false),
    }),
    { organization_settings: false, notification_preferences: false },
  ),
});

/** Accepts QUEUED or queued: the backend may send either casing. Unrecognised values become 'unknown'. */
const deploymentStatus: Decoder<DeploymentStatus> = (value, path) =>
  oneOfOr(DEPLOYMENT_STATUSES, 'unknown')(string(value, path).toLowerCase(), path);

const deploymentRef = object<DeploymentRef>({
  id: string,
  status: deploymentStatus,
  created_at: dateTime,
});

export const applicationSummary = object<ApplicationSummary>({
  id: string,
  name: string,
  description: nullable(string),
  application_type: oneOfOr(APPLICATION_TYPES, 'web_service'),
  runtime: oneOfOr(RUNTIME_TYPES, 'other'),
  provider: providerKind,
  connection_id: string,
  region: string,
  status: oneOfOr(APPLICATION_STATUSES, 'unknown'),
  health: withDefault(oneOfOr(HEALTH_STATUSES, 'unknown'), 'unknown'),
  current_version: nullable(string),
  latest_deployment: nullable(deploymentRef),
  updated_at: dateTime,
});

const applicationSource = object<ApplicationSource>({
  type: oneOf(['image', 'repository'] as const),
  image: nullable(string),
  repository_url: nullable(string),
  ref_type: oneOfOrNull(['branch', 'commit'] as const),
  git_ref: nullable(string),
  dockerfile_path: nullable(string),
  build_context: nullable(string),
});

const appEnvironment = oneOfOr(['production', 'staging', 'preview'] as const, 'production');

export const application: Decoder<Application> = (value, path) => ({
  ...applicationSummary(value, path),
  ...object<Omit<Application, keyof ApplicationSummary>>({
    source: applicationSource,
    image_digest: nullable(string),
    service_url: nullable(string),
    created_at: dateTime,
    created_by: nullable(string),
    environment: withDefault(appEnvironment, 'production'),
    promote_to_id: withDefault(nullable(string), null),
    preview_of_id: withDefault(nullable(string), null),
    preview_number: withDefault(nullable(integer), null),
    paused: withDefault(boolean, false),
    requires_approval: withDefault(boolean, false),
    auto_deploy: withDefault(boolean, false),
  })(value, path),
});

const exposure = oneOfOr(['public', 'private'] as const, 'public');

const configAdjustment = object<ConfigAdjustment>({
  field: string,
  requested: string,
  effective: string,
  reason: string,
});

const effectiveConfiguration = object<EffectiveConfiguration>({
  port: integer,
  cpu: string,
  memory: string,
  min_instances: integer,
  max_instances: integer,
  concurrency: nullable(integer),
  exposure,
  health_check_path: nullable(string),
  observed_at: dateTime,
});

export const applicationConfiguration = object<ApplicationConfiguration>({
  application_id: string,
  version: integer,
  port: integer,
  cpu: string,
  memory: string,
  min_instances: integer,
  max_instances: integer,
  concurrency: nullable(integer),
  exposure,
  health_check_path: nullable(string),
  environment: array(object({ name: string, value: string })),
  secret_refs: withDefault(array(object({ name: string, reference: string })), []),
  updated_at: dateTime,
  effective: nullable(effectiveConfiguration),
  adjustments: withDefault(array(configAdjustment), []),
});

export const specValidationResult = object<SpecValidationResult>({
  valid: boolean,
  issues: withDefault(
    array(
      object({
        field: nullable(string),
        severity: oneOfOr(['error', 'warning'] as const, 'warning'),
        message: string,
      }),
    ),
    [],
  ),
  adjustments: withDefault(array(configAdjustment), []),
});

export const providerCapabilities = object<ProviderCapabilities>({
  provider: providerKind,
  regions: array(string),
  cpu_options: array(string),
  memory_options: array(string),
  min_instances_floor: integer,
  max_instances_limit: integer,
  supports_concurrency: boolean,
  max_concurrency: nullable(integer),
  supports_private_exposure: boolean,
  supported_application_types: (value, path) =>
    array(string)(value, path).filter((t): t is ApplicationType => APPLICATION_TYPES.includes(t as ApplicationType)),
  supports_secret_references: boolean,
  notes: withDefault(array(string), []),
  // A backend without the field offers none of the optional features.
  features: withDefault(
    object<ProviderFeatures>({
      repository_builds: withDefault(boolean, true),
      metrics: withDefault(boolean, false),
      custom_domains: withDefault(boolean, false),
      pause: withDefault(boolean, false),
    }),
    { repository_builds: true, metrics: false, custom_domains: false, pause: false },
  ),
});

const logEntry = object<LogEntry>({
  timestamp: dateTime,
  stream: oneOfOr(['stdout', 'stderr', 'system'] as const, 'stdout'),
  message: string,
});

export const logPage = object<LogPage>({
  entries: array(logEntry),
  truncated: withDefault(boolean, false),
});

export const operationAccepted = object<OperationAccepted>({
  operation_id: string,
  status: oneOf(['accepted'] as const),
});

const deploymentStage = object<DeploymentStage>({
  name: oneOf(STAGE_NAMES),
  status: oneOfOr(STAGE_STATUSES, 'pending'),
  started_at: nullable(dateTime),
  finished_at: nullable(dateTime),
});

export const deploymentSummary = object<DeploymentSummary>({
  id: string,
  application_id: string,
  application_name: string,
  provider: providerKind,
  region: nullable(string),
  status: deploymentStatus,
  kind: oneOfOr(['deploy', 'rollback'] as const, 'deploy'),
  version: nullable(string),
  image_digest: nullable(string),
  source_ref: nullable(string),
  initiated_by: nullable(string),
  created_at: dateTime,
  finished_at: nullable(dateTime),
  trigger: withDefault(oneOfOr(['manual', 'push', 'pull_request', 'promotion', 'api'] as const, 'manual'), 'manual'),
});

const progressPercent: Decoder<number | null> = (value, path) => {
  const n = nullable(number)(value, path);
  if (n === null) return null;
  return n >= 0 && n <= 100 ? n : null;
};

export const deployment: Decoder<Deployment> = (value, path) => ({
  ...deploymentSummary(value, path),
  ...object<
    Pick<
      Deployment,
      | 'rollback_of'
      | 'started_at'
      | 'stages'
      | 'failure'
      | 'service_url'
      | 'progress_percent'
      | 'allowed_actions'
      | 'approved_by'
      | 'approved_at'
    >
  >({
    rollback_of: nullable(string),
    started_at: nullable(dateTime),
    stages: withDefault(array(deploymentStage), []),
    failure: nullable(object({ code: string, message: string })),
    service_url: nullable(string),
    progress_percent: progressPercent,
    // Actions the backend does not explicitly permit are not offered.
    allowed_actions: withDefault(
      object<Deployment['allowed_actions']>({
        rollback: withDefault(boolean, false),
        cancel: withDefault(boolean, false),
        approve: withDefault(boolean, false),
      }),
      { rollback: false, cancel: false, approve: false },
    ),
    approved_by: withDefault(nullable(string), null),
    approved_at: withDefault(nullable(dateTime), null),
  })(value, path),
});

export const deploymentEvent = object<DeploymentEvent>({
  id: string,
  timestamp: dateTime,
  level: oneOfOr(['info', 'warning', 'error'] as const, 'info'),
  stage: oneOfOrNull(STAGE_NAMES),
  message: string,
});

const validationCheck = object<ValidationCheck>({
  name: string,
  status: oneOfOr<CheckStatus>(['pending', 'passed', 'failed', 'skipped', 'unknown'], 'unknown'),
  detail: nullable(string),
});

const connectionValidation = object<ConnectionValidation>({
  stage: oneOfOrNull<ValidationStage>(['connecting', 'validating_permissions', 'checking_readiness']),
  started_at: nullable(dateTime),
  finished_at: nullable(dateTime),
  checks: withDefault(array(validationCheck), []),
  error_code: nullable(string),
});

const connectionConfig = object<ConnectionConfig>({
  account_id: nullable(string),
  role_arn: nullable(string),
  project_id: nullable(string),
  workload_identity_provider: nullable(string),
  service_account_email: nullable(string),
  tenant_id: withDefault(nullable(string), null),
  subscription_id: withDefault(nullable(string), null),
  client_id: withDefault(nullable(string), null),
  resource_group: withDefault(nullable(string), null),
  environment_name: withDefault(nullable(string), null),
  registry_name: withDefault(nullable(string), null),
  ecs_cluster: withDefault(nullable(string), null),
  api_server: withDefault(nullable(string), null),
  namespace: withDefault(nullable(string), null),
  kubernetes_auth: withDefault(oneOfOrNull(['aks', 'oidc'] as const), null),
  token_audience: withDefault(nullable(string), null),
});

export const connectionSetup = object<ConnectionSetup>({
  external_id: nullable(string),
  trusted_principal: nullable(string),
  federation_issuer: withDefault(nullable(string), null),
  federation_audience: withDefault(nullable(string), null),
  aws: withDefault(
    nullable(
      object<AwsSetup>({
        ecr_repository_prefix: string,
        apprunner_access_role: string,
        apprunner_instance_role: string,
        codebuild_role: string,
        ecs_execution_role: string,
        ecs_infrastructure_role: string,
      }),
    ),
    null,
  ),
});

export const providerConnection = object<ProviderConnection>({
  id: string,
  name: string,
  provider: providerKind,
  status: oneOfOr(CONNECTION_STATUSES, 'unknown'),
  default_region: string,
  config: withDefault(connectionConfig, {
    account_id: null,
    role_arn: null,
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
  }),
  setup: nullable(connectionSetup),
  validation: nullable(connectionValidation),
  created_at: dateTime,
  last_validated_at: nullable(dateTime),
});

export const auditEvent = object<AuditEvent>({
  id: string,
  occurred_at: dateTime,
  actor: nullable(object({ id: string, display_name: string, email: nullable(string) })),
  action: string,
  resource_type: string,
  resource_id: nullable(string),
  resource_name: nullable(string),
  outcome: oneOfOr(AUDIT_OUTCOMES, 'unknown'),
  request_id: nullable(string),
});

export const organization = object<Organization>({ id: string, name: string, created_at: dateTime });

export const organizationMember = object<OrganizationMember>({
  id: string,
  display_name: string,
  email: string,
  role: oneOfOr(USER_ROLES, 'viewer'),
  joined_at: dateTime,
});

export const notificationPreferences = object<NotificationPreferences>({
  email_enabled: boolean,
  events: record(NOTIFICATION_EVENTS, withDefault(boolean, false)),
});

/* ---------- Platform features ---------- */

export const automation = object<Automation>({
  environment: appEnvironment,
  promote_to_id: nullable(string),
  auto_deploy: boolean,
  auto_deploy_branch: nullable(string),
  previews_enabled: boolean,
  config_from_repo: boolean,
  requires_approval: boolean,
  webhook: object({ url: string, secret_configured: boolean }),
});

export const webhookSecret = object<WebhookSecret>({ url: string, secret: string });

export const scheduleState = object<ScheduleState>({
  schedule: nullable(
    object({ timezone: string, days: array(integer), sleep_at: string, wake_at: string }),
  ),
  paused: boolean,
  paused_by_schedule: boolean,
  asleep_now: boolean,
});

const METRIC_NAMES: readonly MetricName[] = [
  'requests',
  'errors_4xx',
  'errors_5xx',
  'latency_ms',
  'instances',
  'cpu_percent',
  'memory_percent',
];

export const metrics: Decoder<Metrics> = (value, path) => {
  const raw = object({
    range: oneOfOr(['1h', '6h', '24h', '7d'] as const, '1h'),
    step_seconds: integer,
    series: array(
      object({
        name: string,
        unit: string,
        points: array(object({ timestamp: dateTime, value: number })),
      }),
    ),
    unavailable_reason: nullable(string),
  })(value, path);
  // Series a newer backend adds are ignored until the UI knows how to show them.
  return {
    ...raw,
    series: raw.series.filter((s): s is Metrics['series'][number] => METRIC_NAMES.includes(s.name as MetricName)),
  };
};

export const costEstimate = object<CostEstimate>({
  provider: providerKind,
  currency: oneOf(['USD'] as const),
  monthly_low: nullable(number),
  monthly_high: nullable(number),
  lines: withDefault(array(object({ item: string, low: number, high: number })), []),
  assumptions: withDefault(array(string), []),
});

export const customDomain = object<CustomDomain>({
  id: string,
  domain: string,
  status: oneOfOr(['pending_dns', 'verifying', 'active', 'failed', 'removing'] as const, 'pending_dns'),
  records: withDefault(array(object({ type: string, name: string, value: string, purpose: string })), []),
  message: nullable(string),
  created_at: dateTime,
  checked_at: nullable(dateTime),
});

const notificationEvents: Decoder<AlertChannel['events']> = (value, path) =>
  array(string)(value, path).filter((e): e is AlertChannel['events'][number] =>
    NOTIFICATION_EVENTS.includes(e as AlertChannel['events'][number]),
  );

const alertChannelFields = {
  id: string,
  name: string,
  kind: oneOf(['slack', 'teams', 'webhook'] as const),
  host: string,
  events: notificationEvents,
  enabled: boolean,
  signed: withDefault(boolean, false),
  created_at: dateTime,
  last_delivery_at: nullable(dateTime),
  last_delivery_status: nullable(string),
};

export const alertChannel = object<AlertChannel>(alertChannelFields);
export const createdAlertChannel = object<CreatedAlertChannel>({
  ...alertChannelFields,
  signing_secret: withDefault(nullable(string), null),
});

export const alertTest = object<{ delivered: boolean; status: string }>({ delivered: boolean, status: string });

const apiTokenFields = {
  id: string,
  name: string,
  prefix: string,
  created_at: dateTime,
  last_used_at: nullable(dateTime),
  expires_at: nullable(dateTime),
};

export const apiToken = object<ApiToken>(apiTokenFields);
export const createdApiToken = object<CreatedApiToken>({ ...apiTokenFields, token: string });
