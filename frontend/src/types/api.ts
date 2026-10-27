/**
 * Wire types for the CloudBridge REST API (see docs/api-contract.md).
 * Field names match the JSON the FastAPI backend sends, so they are snake_case.
 * Every response is checked at runtime by the decoders in services/api/decoders.ts.
 */

export type IsoDateTime = string;

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/* ---------- Session and users ---------- */

export type UserRole = 'owner' | 'admin' | 'developer' | 'viewer';

export type Permission =
  | 'applications:create'
  | 'applications:update'
  | 'applications:delete'
  | 'deployments:create'
  | 'deployments:cancel'
  | 'deployments:rollback'
  | 'deployments:approve'
  | 'providers:manage'
  | 'audit:read'
  | 'organization:manage';

export type TimeFormat = '12h' | '24h';

export interface UserPreferences {
  timezone: string | null;
  time_format: TimeFormat;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  preferences: UserPreferences;
}

export interface OrganizationRef {
  id: string;
  name: string;
}

export interface SessionFeatures {
  organization_settings: boolean;
  notification_preferences: boolean;
}

export interface Session {
  user: User;
  organization: OrganizationRef;
  permissions: Permission[];
  features: SessionFeatures;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/* ---------- Providers ---------- */

export type ProviderKind = 'aws_app_runner' | 'gcp_cloud_run' | 'azure_container_apps' | 'aws_ecs_express' | 'kubernetes';

export type ConnectionStatus =
  | 'pending_validation'
  | 'validating'
  | 'connected'
  | 'invalid_credentials'
  | 'insufficient_permissions'
  | 'provider_unavailable'
  | 'timed_out'
  | 'disconnected'
  | 'error'
  | 'unknown';

export type ValidationStage = 'connecting' | 'validating_permissions' | 'checking_readiness';

export type CheckStatus = 'pending' | 'passed' | 'failed' | 'skipped' | 'unknown';

export interface ValidationCheck {
  name: string;
  status: CheckStatus;
  detail: string | null;
}

export interface ConnectionValidation {
  stage: ValidationStage | null;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  checks: ValidationCheck[];
  error_code: string | null;
}

export interface ConnectionSetup {
  /** AWS: the external ID the IAM role's trust policy must require. */
  external_id: string | null;
  /** The CloudBridge principal the role or workload identity pool must trust (Azure: the federated subject). */
  trusted_principal: string | null;
  /** Azure: the issuer the app registration's federated credential must trust. */
  federation_issuer: string | null;
  /** Azure: the audience of that federated credential. */
  federation_audience: string | null;
  /** AWS: the names CloudBridge uses in the account (the setup script creates these roles). */
  aws: AwsSetup | null;
}

export interface AwsSetup {
  ecr_repository_prefix: string;
  apprunner_access_role: string;
  apprunner_instance_role: string;
  codebuild_role: string;
  ecs_execution_role: string;
  ecs_infrastructure_role: string;
}

export interface ConnectionConfig {
  account_id: string | null;
  role_arn: string | null;
  project_id: string | null;
  workload_identity_provider: string | null;
  service_account_email: string | null;
  tenant_id: string | null;
  subscription_id: string | null;
  client_id: string | null;
  resource_group: string | null;
  environment_name: string | null;
  registry_name: string | null;
  ecs_cluster: string | null;
  api_server: string | null;
  namespace: string | null;
  kubernetes_auth: 'aks' | 'oidc' | null;
  token_audience: string | null;
}

export interface ProviderConnection {
  id: string;
  name: string;
  provider: ProviderKind;
  status: ConnectionStatus;
  default_region: string;
  config: ConnectionConfig;
  setup: ConnectionSetup | null;
  validation: ConnectionValidation | null;
  created_at: IsoDateTime;
  last_validated_at: IsoDateTime | null;
}

export interface CreateConnectionRequest {
  name: string;
  provider: ProviderKind;
  default_region: string;
  aws?: { account_id: string; role_arn: string };
  gcp?: { project_id: string; workload_identity_provider: string; service_account_email: string };
  azure?: {
    tenant_id: string;
    subscription_id: string;
    client_id: string;
    resource_group: string;
    environment_name: string;
    registry_name?: string | null;
  };
  ecs?: { account_id: string; role_arn: string; cluster?: string | null };
  kubernetes?: {
    api_server: string;
    ca_certificate: string;
    namespace: string;
    auth: 'aks' | 'oidc';
    tenant_id?: string | null;
    client_id?: string | null;
    audience?: string | null;
  };
}

/* ---------- Provider capabilities ---------- */

/**
 * What a provider supports, as reported by GET /providers/{provider}/capabilities.
 * The frontend never hardcodes these limits: warnings are computed from this data.
 */
export interface ProviderCapabilities {
  provider: ProviderKind;
  regions: string[];
  cpu_options: string[];
  memory_options: string[];
  /** Lowest allowed minimum instance count (0 means scale-to-zero is supported). */
  min_instances_floor: number;
  max_instances_limit: number;
  supports_concurrency: boolean;
  max_concurrency: number | null;
  supports_private_exposure: boolean;
  supported_application_types: ApplicationType[];
  supports_secret_references: boolean;
  notes: string[];
  /** Optional platform features the provider offers through CloudBridge. */
  features: ProviderFeatures;
}

export interface ProviderFeatures {
  repository_builds: boolean;
  metrics: boolean;
  custom_domains: boolean;
  pause: boolean;
}

/* ---------- Applications ---------- */

export type ApplicationStatus =
  | 'draft'
  | 'deploying'
  | 'running'
  | 'failed'
  | 'stopped'
  | 'deleting'
  | 'unknown';

/** Result of the backend's health checks against the running service. */
export type HealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown';

export type ApplicationType = 'web_service' | 'api' | 'worker';

export type RuntimeType = 'node' | 'python' | 'java' | 'go' | 'dotnet' | 'ruby' | 'php' | 'other';

export type Exposure = 'public' | 'private';

export type SourceType = 'image' | 'repository';

export type GitRefType = 'branch' | 'commit';

export interface ApplicationSource {
  type: SourceType;
  image: string | null;
  repository_url: string | null;
  ref_type: GitRefType | null;
  /** A branch name, or an immutable commit SHA when ref_type is 'commit'. */
  git_ref: string | null;
  dockerfile_path: string | null;
  build_context: string | null;
}

export interface DeploymentRef {
  id: string;
  status: DeploymentStatus;
  created_at: IsoDateTime;
}

export interface ApplicationSummary {
  id: string;
  name: string;
  description: string | null;
  application_type: ApplicationType;
  runtime: RuntimeType;
  provider: ProviderKind;
  connection_id: string;
  region: string;
  status: ApplicationStatus;
  health: HealthStatus;
  /** Version label of the revision currently serving traffic, if any. */
  current_version: string | null;
  latest_deployment: DeploymentRef | null;
  updated_at: IsoDateTime;
}

export interface Application extends ApplicationSummary {
  source: ApplicationSource;
  /** Immutable digest (sha256:…) of the image currently serving traffic. */
  image_digest: string | null;
  service_url: string | null;
  created_at: IsoDateTime;
  created_by: string | null;
  environment: AppEnvironment;
  promote_to_id: string | null;
  preview_of_id: string | null;
  preview_number: number | null;
  paused: boolean;
  requires_approval: boolean;
  auto_deploy: boolean;
}

export type AppEnvironment = 'production' | 'staging' | 'preview';

export type SortDirection = 'asc' | 'desc';

export interface SortParam<K extends string> {
  key: K;
  direction: SortDirection;
}

export type ApplicationSortKey = 'name' | 'updated_at' | 'health';

export interface ListApplicationsParams {
  page: number;
  page_size: number;
  search?: string;
  status?: ApplicationStatus;
  health?: HealthStatus[];
  provider?: ProviderKind;
  sort?: SortParam<ApplicationSortKey>;
}

export interface EnvironmentVariable {
  name: string;
  value: string;
}

/**
 * A pointer to a secret held in the provider's secret manager, exposed to the
 * application as an environment variable. The value itself never passes through CloudBridge.
 */
export interface SecretReference {
  name: string;
  /** e.g. arn:aws:secretsmanager:…:secret:… or projects/…/secrets/…/versions/… */
  reference: string;
}

/** Settings the user asks for. The provider may apply them differently (see EffectiveConfiguration). */
export interface RequestedConfiguration {
  port: number;
  cpu: string;
  memory: string;
  min_instances: number;
  max_instances: number;
  /** Requests per instance. Null lets the provider choose. */
  concurrency: number | null;
  exposure: Exposure;
  health_check_path: string | null;
  environment: EnvironmentVariable[];
  secret_refs: SecretReference[];
}

export interface CreateApplicationRequest extends RequestedConfiguration {
  name: string;
  description: string | null;
  application_type: ApplicationType;
  runtime: RuntimeType;
  connection_id: string;
  region: string;
  source: ApplicationSource;
}

/** What the provider actually runs, as observed by the backend after the last deployment. */
export interface EffectiveConfiguration {
  port: number;
  cpu: string;
  memory: string;
  min_instances: number;
  max_instances: number;
  concurrency: number | null;
  exposure: Exposure;
  health_check_path: string | null;
  observed_at: IsoDateTime;
}

/** A requested setting the provider changed or ignored. */
export interface ConfigAdjustment {
  field: string;
  requested: string;
  effective: string;
  reason: string;
}

/** GET/PUT /apps/{id}/spec */
export interface ApplicationConfiguration extends RequestedConfiguration {
  application_id: string;
  version: number;
  updated_at: IsoDateTime;
  /** Null until the application has been deployed. */
  effective: EffectiveConfiguration | null;
  adjustments: ConfigAdjustment[];
}

export interface UpdateConfigurationRequest extends RequestedConfiguration {
  version: number;
}

export type IssueSeverity = 'error' | 'warning';

export interface SpecIssue {
  field: string | null;
  severity: IssueSeverity;
  message: string;
}

/** POST /apps/{id}/validate */
export interface SpecValidationResult {
  valid: boolean;
  issues: SpecIssue[];
  adjustments: ConfigAdjustment[];
}

export type LogStream = 'stdout' | 'stderr' | 'system';

export interface LogEntry {
  timestamp: IsoDateTime;
  stream: LogStream;
  message: string;
}

export interface LogPage {
  entries: LogEntry[];
  /** True when older entries exist that were not returned. */
  truncated: boolean;
}

/** Returned with 202 when the backend accepts a long-running operation. */
export interface OperationAccepted {
  operation_id: string;
  status: 'accepted';
}

/* ---------- Deployments ---------- */

export type DeploymentStatus =
  | 'awaiting_approval'
  | 'queued'
  | 'validating'
  | 'building'
  | 'publishing'
  | 'deploying'
  | 'verifying'
  | 'succeeded'
  | 'failed'
  | 'rolling_back'
  | 'rolled_back'
  | 'failed_rollback'
  | 'cancelled'
  | 'unknown';

export type DeploymentKind = 'deploy' | 'rollback';

export type StageName =
  | 'queued'
  | 'validation'
  | 'build'
  | 'publishing'
  | 'provisioning'
  | 'verification';

export type StageStatus = 'pending' | 'active' | 'succeeded' | 'failed' | 'cancelled' | 'skipped';

export interface DeploymentStage {
  name: StageName;
  status: StageStatus;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
}

export interface DeploymentFailure {
  code: string;
  message: string;
}

/** Operations the backend permits on this deployment for the current user. Absent means not permitted. */
export interface DeploymentActions {
  rollback: boolean;
  cancel: boolean;
  approve: boolean;
}

export type DeploymentTrigger = 'manual' | 'push' | 'pull_request' | 'promotion' | 'api';

export interface DeploymentSummary {
  id: string;
  application_id: string;
  application_name: string;
  provider: ProviderKind;
  region: string | null;
  status: DeploymentStatus;
  kind: DeploymentKind;
  version: string | null;
  image_digest: string | null;
  source_ref: string | null;
  initiated_by: string | null;
  created_at: IsoDateTime;
  finished_at: IsoDateTime | null;
  trigger: DeploymentTrigger;
}

export interface Deployment extends DeploymentSummary {
  rollback_of: string | null;
  started_at: IsoDateTime | null;
  stages: DeploymentStage[];
  failure: DeploymentFailure | null;
  service_url: string | null;
  /** Only present when the provider reports real progress. Never estimated. */
  progress_percent: number | null;
  allowed_actions: DeploymentActions;
  approved_by: string | null;
  approved_at: IsoDateTime | null;
}

export type EventLevel = 'info' | 'warning' | 'error';

export interface DeploymentEvent {
  id: string;
  timestamp: IsoDateTime;
  level: EventLevel;
  stage: StageName | null;
  message: string;
}

export type DeploymentSortKey = 'created_at' | 'application_name' | 'status';

export interface ListDeploymentsParams {
  page: number;
  page_size: number;
  application_id?: string;
  status?: DeploymentStatus[];
  provider?: ProviderKind;
  search?: string;
  sort?: SortParam<DeploymentSortKey>;
}

/* ---------- Audit ---------- */

export type AuditOutcome = 'success' | 'failure' | 'denied' | 'unknown';

export interface AuditActor {
  id: string;
  display_name: string;
  email: string | null;
}

export interface AuditEvent {
  id: string;
  occurred_at: IsoDateTime;
  actor: AuditActor | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  resource_name: string | null;
  outcome: AuditOutcome;
  request_id: string | null;
}

export interface ListAuditEventsParams {
  page: number;
  page_size: number;
  search?: string;
  actor?: string;
  action?: string;
  outcome?: AuditOutcome;
  resource_type?: string;
  from?: string;
  to?: string;
}

/* ---------- Settings ---------- */

export interface UpdateProfileRequest {
  display_name: string;
  preferences: UserPreferences;
}

export interface Organization {
  id: string;
  name: string;
  created_at: IsoDateTime;
}

export interface OrganizationMember {
  id: string;
  display_name: string;
  email: string;
  role: UserRole;
  joined_at: IsoDateTime;
}

export type NotificationEvent =
  | 'deployment_succeeded'
  | 'deployment_failed'
  | 'rollback_completed'
  | 'provider_connection_failed'
  | 'application_deleted'
  | 'application_unhealthy'
  | 'approval_requested';

export interface NotificationPreferences {
  email_enabled: boolean;
  events: Record<NotificationEvent, boolean>;
}

/* ---------- Automation, environments, schedules ---------- */

export interface Automation {
  environment: AppEnvironment;
  promote_to_id: string | null;
  auto_deploy: boolean;
  auto_deploy_branch: string | null;
  previews_enabled: boolean;
  config_from_repo: boolean;
  requires_approval: boolean;
  webhook: { url: string; secret_configured: boolean };
}

export type AutomationInput = Omit<Automation, 'webhook'>;

export interface WebhookSecret {
  url: string;
  /** Shown once. */
  secret: string;
}

export interface SleepSchedule {
  timezone: string;
  /** 0 = Monday. */
  days: number[];
  sleep_at: string;
  wake_at: string;
}

export interface ScheduleState {
  schedule: SleepSchedule | null;
  paused: boolean;
  paused_by_schedule: boolean;
  asleep_now: boolean;
}

/* ---------- Metrics and cost ---------- */

export type MetricRange = '1h' | '6h' | '24h' | '7d';
export type MetricName =
  | 'requests'
  | 'errors_4xx'
  | 'errors_5xx'
  | 'latency_ms'
  | 'instances'
  | 'cpu_percent'
  | 'memory_percent';

export interface MetricSeries {
  name: MetricName;
  unit: string;
  points: { timestamp: IsoDateTime; value: number }[];
}

export interface Metrics {
  range: MetricRange;
  step_seconds: number;
  series: MetricSeries[];
  unavailable_reason: string | null;
}

export interface CostEstimate {
  provider: ProviderKind;
  currency: 'USD';
  monthly_low: number | null;
  monthly_high: number | null;
  lines: { item: string; low: number; high: number }[];
  assumptions: string[];
}

export interface CostEstimateRequest {
  provider: ProviderKind;
  cpu: string;
  memory: string;
  min_instances: number;
  max_instances: number;
}

/* ---------- Custom domains ---------- */

export type DomainStatus = 'pending_dns' | 'verifying' | 'active' | 'failed' | 'removing';

export interface DnsRecord {
  type: string;
  name: string;
  value: string;
  purpose: string;
}

export interface CustomDomain {
  id: string;
  domain: string;
  status: DomainStatus;
  records: DnsRecord[];
  message: string | null;
  created_at: IsoDateTime;
  checked_at: IsoDateTime | null;
}

/* ---------- Alert channels and API tokens ---------- */

export type AlertChannelKind = 'slack' | 'teams' | 'webhook';

export interface AlertChannel {
  id: string;
  name: string;
  kind: AlertChannelKind;
  /** Only the host: the full webhook address is never returned. */
  host: string;
  events: NotificationEvent[];
  enabled: boolean;
  signed: boolean;
  created_at: IsoDateTime;
  last_delivery_at: IsoDateTime | null;
  last_delivery_status: string | null;
}

export interface AlertChannelInput {
  name: string;
  kind: AlertChannelKind;
  /** Required when creating; omit to keep the current address. */
  url?: string;
  events: NotificationEvent[];
  enabled: boolean;
}

export interface CreatedAlertChannel extends AlertChannel {
  /** Generic webhooks only, shown once. */
  signing_secret: string | null;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: IsoDateTime;
  last_used_at: IsoDateTime | null;
  expires_at: IsoDateTime | null;
}

export interface CreatedApiToken extends ApiToken {
  /** Shown once. */
  token: string;
}
