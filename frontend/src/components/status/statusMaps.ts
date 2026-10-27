import type {
  ApplicationStatus,
  AuditOutcome,
  CheckStatus,
  ConnectionStatus,
  DeploymentStatus,
  HealthStatus,
  StageName,
  StageStatus,
} from '../../types/api';
import { DEPLOYMENT_STATUSES } from '../../services/api/decoders';

export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface StatusInfo {
  label: string;
  tone: StatusTone;
  /** True while work is still happening (shows a spinner instead of a static icon). */
  busy?: boolean;
}

export const applicationStatus: Record<ApplicationStatus, StatusInfo> = {
  draft: { label: 'Not deployed', tone: 'neutral' },
  deploying: { label: 'Deploying', tone: 'info', busy: true },
  running: { label: 'Running', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  stopped: { label: 'Stopped', tone: 'neutral' },
  deleting: { label: 'Deleting', tone: 'warning', busy: true },
  unknown: { label: 'Unknown', tone: 'neutral' },
};

export const healthStatus: Record<HealthStatus, StatusInfo> = {
  healthy: { label: 'Healthy', tone: 'success' },
  degraded: { label: 'Degraded', tone: 'warning' },
  unhealthy: { label: 'Unhealthy', tone: 'danger' },
  unknown: { label: 'No health data', tone: 'neutral' },
};

export const deploymentStatus: Record<DeploymentStatus, StatusInfo> = {
  awaiting_approval: { label: 'Awaiting approval', tone: 'warning' },
  queued: { label: 'Queued', tone: 'info', busy: true },
  validating: { label: 'Validating', tone: 'info', busy: true },
  building: { label: 'Building', tone: 'info', busy: true },
  publishing: { label: 'Publishing', tone: 'info', busy: true },
  deploying: { label: 'Deploying', tone: 'info', busy: true },
  verifying: { label: 'Verifying', tone: 'info', busy: true },
  succeeded: { label: 'Succeeded', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  rolling_back: { label: 'Rolling back', tone: 'warning', busy: true },
  rolled_back: { label: 'Rolled back', tone: 'warning' },
  failed_rollback: { label: 'Rollback failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  unknown: { label: 'Unknown', tone: 'neutral' },
};

/** Statuses in which the backend is still working on the deployment. */
export const ACTIVE_DEPLOYMENT_STATUSES: readonly DeploymentStatus[] = [
  'awaiting_approval',
  'queued',
  'validating',
  'building',
  'publishing',
  'deploying',
  'verifying',
  'rolling_back',
];

export const FAILED_DEPLOYMENT_STATUSES: readonly DeploymentStatus[] = ['failed', 'failed_rollback'];

/** Named status groups usable as a single filter value in URLs (e.g. ?status=active). */
export const DEPLOYMENT_STATUS_GROUPS = {
  active: { label: 'All in progress', statuses: ACTIVE_DEPLOYMENT_STATUSES },
  failed: { label: 'All failures', statuses: FAILED_DEPLOYMENT_STATUSES },
} as const;

export type DeploymentStatusGroup = keyof typeof DEPLOYMENT_STATUS_GROUPS;

/** A single status, or a named group such as "active" or "failed", from a URL filter value. */
export function parseStatusFilter(value: string): DeploymentStatus[] | undefined {
  if (Object.hasOwn(DEPLOYMENT_STATUS_GROUPS, value)) return [...DEPLOYMENT_STATUS_GROUPS[value as DeploymentStatusGroup].statuses];
  return (DEPLOYMENT_STATUSES as readonly string[]).includes(value) ? [value as DeploymentStatus] : undefined;
}

export const connectionStatus: Record<ConnectionStatus, StatusInfo> = {
  pending_validation: { label: 'Not validated', tone: 'warning' },
  validating: { label: 'Validating', tone: 'info', busy: true },
  connected: { label: 'Connected', tone: 'success' },
  invalid_credentials: { label: 'Invalid credentials', tone: 'danger' },
  insufficient_permissions: { label: 'Missing permissions', tone: 'danger' },
  provider_unavailable: { label: 'Provider unreachable', tone: 'warning' },
  timed_out: { label: 'Validation timed out', tone: 'warning' },
  disconnected: { label: 'Disconnected', tone: 'neutral' },
  error: { label: 'Error', tone: 'danger' },
  unknown: { label: 'Unknown', tone: 'neutral' },
};

export const auditOutcome: Record<AuditOutcome, StatusInfo> = {
  success: { label: 'Success', tone: 'success' },
  failure: { label: 'Failure', tone: 'danger' },
  denied: { label: 'Denied', tone: 'warning' },
  unknown: { label: 'Unknown', tone: 'neutral' },
};

export const stageStatus: Record<StageStatus, StatusInfo> = {
  pending: { label: 'Pending', tone: 'neutral' },
  active: { label: 'In progress', tone: 'info', busy: true },
  succeeded: { label: 'Completed', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  skipped: { label: 'Skipped', tone: 'neutral' },
};

export const checkStatus: Record<CheckStatus, StatusInfo> = {
  pending: { label: 'Pending', tone: 'neutral' },
  passed: { label: 'Passed', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  skipped: { label: 'Skipped', tone: 'neutral' },
  unknown: { label: 'Unknown', tone: 'neutral' },
};

export const stageLabel: Record<StageName, string> = {
  queued: 'Queued',
  validation: 'Validation',
  build: 'Build',
  publishing: 'Publishing',
  provisioning: 'Provisioning',
  verification: 'Verification',
};

export const TERMINAL_DEPLOYMENT_STATUSES: readonly DeploymentStatus[] = [
  'succeeded',
  'failed',
  'rolled_back',
  'failed_rollback',
  'cancelled',
];

export function isDeploymentActive(status: DeploymentStatus): boolean {
  return ACTIVE_DEPLOYMENT_STATUSES.includes(status);
}

export function isConnectionUsable(status: ConnectionStatus): boolean {
  return status === 'connected';
}
