import type {
  AlertChannel,
  AlertChannelInput,
  ApiToken,
  Automation,
  AutomationInput,
  CostEstimate,
  ConnectionSetup,
  CostEstimateRequest,
  CreatedAlertChannel,
  CreatedApiToken,
  CustomDomain,
  MetricRange,
  Metrics,
  ScheduleState,
  SleepSchedule,
  WebhookSecret,
  Application,
  ApplicationConfiguration,
  ApplicationSummary,
  AuditEvent,
  CreateApplicationRequest,
  CreateConnectionRequest,
  Deployment,
  DeploymentEvent,
  DeploymentSummary,
  ListApplicationsParams,
  ListAuditEventsParams,
  ListDeploymentsParams,
  LogPage,
  LoginRequest,
  NotificationPreferences,
  Organization,
  OrganizationMember,
  Paginated,
  ProviderCapabilities,
  ProviderConnection,
  ProviderKind,
  Session,
  SpecValidationResult,
  UpdateConfigurationRequest,
  UpdateProfileRequest,
  User,
} from '../../types/api';

export type DeleteOutcome = 'deleted' | 'accepted';

/** Options for state-changing calls that the backend can de-duplicate. */
export interface WriteOptions {
  idempotencyKey: string;
}

export interface AuthApi {
  getSession(signal?: AbortSignal): Promise<Session>;
  login(input: LoginRequest): Promise<Session>;
  logout(): Promise<void>;
}

export interface ApplicationsApi {
  list(params: ListApplicationsParams, signal?: AbortSignal): Promise<Paginated<ApplicationSummary>>;
  get(id: string, signal?: AbortSignal): Promise<Application>;
  create(input: CreateApplicationRequest, options: WriteOptions): Promise<Application>;
  /** 'deleted' (204) when removal finished, 'accepted' (202) when it continues in the background. */
  remove(id: string): Promise<DeleteOutcome>;
  /** The requested spec plus, once deployed, the effective provider configuration. */
  getConfiguration(id: string, signal?: AbortSignal): Promise<ApplicationConfiguration>;
  updateConfiguration(id: string, input: UpdateConfigurationRequest): Promise<ApplicationConfiguration>;
  /** Checks the saved spec against the target provider without deploying. */
  validate(id: string): Promise<SpecValidationResult>;
  /** Runtime logs of the running service. Rejects with 404 or 501 when the backend does not support them. */
  getLogs(id: string, signal?: AbortSignal): Promise<LogPage>;
}

export interface DeploymentsApi {
  list(params: ListDeploymentsParams, signal?: AbortSignal): Promise<Paginated<DeploymentSummary>>;
  listForApplication(
    applicationId: string,
    params: Pick<ListDeploymentsParams, 'page' | 'page_size'>,
    signal?: AbortSignal,
  ): Promise<Paginated<DeploymentSummary>>;
  get(id: string, signal?: AbortSignal): Promise<Deployment>;
  listEvents(id: string, signal?: AbortSignal): Promise<DeploymentEvent[]>;
  /** Build and deployment logs. Rejects with 404 or 501 when the backend does not support them. */
  getLogs(id: string, signal?: AbortSignal): Promise<LogPage>;
  /** Accepted (202): the deployment is queued, not finished. */
  create(applicationId: string, options: WriteOptions): Promise<Deployment>;
  cancel(id: string): Promise<Deployment>;
  /** Accepted (202): returns the new rollback deployment. */
  rollback(id: string, options: WriteOptions): Promise<Deployment>;
  /** Accepted (202): an approved deployment is queued for the worker. */
  approve(id: string): Promise<Deployment>;
  reject(id: string): Promise<Deployment>;
}

/** Per-application automation and operations, alert channels and API tokens. */
export interface PlatformApi {
  getAutomation(appId: string, signal?: AbortSignal): Promise<Automation>;
  updateAutomation(appId: string, input: AutomationInput): Promise<Automation>;
  /** Creates a new GitHub webhook secret, shown only in this response. */
  rotateWebhookSecret(appId: string): Promise<WebhookSecret>;
  applyConfigFile(appId: string, input: { content: string; version: number }): Promise<ApplicationConfiguration>;
  listPreviews(appId: string, signal?: AbortSignal): Promise<ApplicationSummary[]>;
  /** Accepted (202): the deployment on the promotion target. */
  promote(appId: string, options: WriteOptions): Promise<Deployment>;
  getSchedule(appId: string, signal?: AbortSignal): Promise<ScheduleState>;
  setSchedule(appId: string, schedule: SleepSchedule): Promise<ScheduleState>;
  deleteSchedule(appId: string): Promise<ScheduleState>;
  pause(appId: string): Promise<ScheduleState>;
  resume(appId: string): Promise<ScheduleState>;
  getMetrics(appId: string, range: MetricRange, signal?: AbortSignal): Promise<Metrics>;
  getCost(appId: string, signal?: AbortSignal): Promise<CostEstimate>;
  estimateCost(input: CostEstimateRequest, signal?: AbortSignal): Promise<CostEstimate>;
  listDomains(appId: string, signal?: AbortSignal): Promise<CustomDomain[]>;
  addDomain(appId: string, domain: string): Promise<CustomDomain>;
  checkDomain(appId: string, domainId: string): Promise<CustomDomain>;
  /** Accepted (202): removal continues in the background. */
  removeDomain(appId: string, domainId: string): Promise<void>;
  listAlertChannels(signal?: AbortSignal): Promise<AlertChannel[]>;
  createAlertChannel(input: AlertChannelInput): Promise<CreatedAlertChannel>;
  updateAlertChannel(id: string, input: AlertChannelInput): Promise<AlertChannel>;
  deleteAlertChannel(id: string): Promise<void>;
  testAlertChannel(id: string): Promise<{ delivered: boolean; status: string }>;
  listApiTokens(signal?: AbortSignal): Promise<ApiToken[]>;
  createApiToken(input: { name: string; expires_in_days: number | null }): Promise<CreatedApiToken>;
  revokeApiToken(id: string): Promise<void>;
}

export interface ProvidersApi {
  list(signal?: AbortSignal): Promise<ProviderConnection[]>;
  getCapabilities(provider: ProviderKind, signal?: AbortSignal): Promise<ProviderCapabilities>;
  /** What the customer's account must trust, before a connection exists (no external ID yet). */
  getSetup(provider: ProviderKind, signal?: AbortSignal): Promise<ConnectionSetup>;
  get(id: string, signal?: AbortSignal): Promise<ProviderConnection>;
  create(input: CreateConnectionRequest, options: WriteOptions): Promise<ProviderConnection>;
  /** Accepted (202): validation runs on the backend; poll get() for progress. */
  validate(id: string): Promise<ProviderConnection>;
  remove(id: string): Promise<DeleteOutcome>;
}

export interface AuditApi {
  list(params: ListAuditEventsParams, signal?: AbortSignal): Promise<Paginated<AuditEvent>>;
}

export interface SettingsApi {
  updateProfile(input: UpdateProfileRequest): Promise<User>;
  getOrganization(signal?: AbortSignal): Promise<Organization>;
  updateOrganization(input: { name: string }): Promise<Organization>;
  listMembers(signal?: AbortSignal): Promise<OrganizationMember[]>;
  getNotificationPreferences(signal?: AbortSignal): Promise<NotificationPreferences>;
  updateNotificationPreferences(input: NotificationPreferences): Promise<NotificationPreferences>;
}

/** Everything the UI can ask of the backend. Implemented over HTTP and, for development only, by the demo service. */
export interface CloudBridgeApi {
  readonly mode: 'live' | 'demo';
  auth: AuthApi;
  applications: ApplicationsApi;
  deployments: DeploymentsApi;
  providers: ProvidersApi;
  audit: AuditApi;
  settings: SettingsApi;
  platform: PlatformApi;
  /** Subscribes to global session-expiry and maintenance signals. */
  on(event: 'session-expired' | 'maintenance', listener: () => void): () => void;
}
