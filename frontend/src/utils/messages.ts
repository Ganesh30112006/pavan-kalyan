import type { ToastInput } from '../app/contexts';

/**
 * Every user-facing notification in one place, so wording stays consistent.
 * "Accepted" messages say the request was taken, never that work finished.
 * In demo mode, `simulated` marks the message so it cannot be read as a real
 * cloud operation.
 */

type Message = Pick<ToastInput, 'tone' | 'title' | 'message'>;

const DEMO_SUFFIX = ' This is a simulated result. No cloud resources were changed.';

export function simulated(message: Message, demo: boolean): Message {
  return demo ? { ...message, message: `${message.message ?? ''}${DEMO_SUFFIX}`.trim() } : message;
}

export const messages = {
  applicationCreated: (name: string): Message => ({
    tone: 'success',
    title: 'Application created',
    message: `${name} was created. Deploy it when you are ready.`,
  }),
  applicationDeleted: (name: string): Message => ({
    tone: 'success',
    title: 'Application deleted',
    message: `${name} was deleted.`,
  }),
  applicationDeletionAccepted: (name: string): Message => ({
    tone: 'info',
    title: 'Deletion requested',
    message: `${name} is being deleted. It may take a few minutes to remove the cloud service.`,
  }),
  configurationSaved: (): Message => ({
    tone: 'success',
    title: 'Configuration saved',
    message: 'Changes take effect on the next deployment.',
  }),
  secretSaved: (name: string): Message => ({
    tone: 'success',
    title: 'Secret saved',
    message: `The value of ${name} was stored. It will not be shown again.`,
  }),
  secretDeleted: (name: string): Message => ({ tone: 'success', title: 'Secret removed', message: `${name} was removed.` }),
  deploymentQueued: (app: string): Message => ({
    tone: 'info',
    title: 'Deployment queued',
    message: `The deployment of ${app} was accepted and is waiting to start.`,
  }),
  deploymentSucceeded: (app: string): Message => ({
    tone: 'success',
    title: 'Deployment succeeded',
    message: `${app} is deployed.`,
  }),
  deploymentFailed: (app: string): Message => ({
    tone: 'error',
    title: 'Deployment failed',
    message: `The deployment of ${app} failed. Review the events for details.`,
  }),
  deploymentCancelRequested: (): Message => ({
    tone: 'info',
    title: 'Cancellation requested',
    message: 'The deployment will stop at the next safe point.',
  }),
  deploymentCancelled: (): Message => ({ tone: 'warning', title: 'Deployment cancelled' }),
  rollbackStarted: (app: string): Message => ({
    tone: 'info',
    title: 'Rollback started',
    message: `A rollback of ${app} was accepted and is waiting to start.`,
  }),
  rollbackCompleted: (app: string): Message => ({
    tone: 'success',
    title: 'Rollback completed',
    message: `${app} was rolled back.`,
  }),
  rollbackFailed: (app: string): Message => ({
    tone: 'error',
    title: 'Rollback failed',
    message: `The rollback of ${app} failed. The previous state may still be running.`,
  }),
  providerCreated: (name: string): Message => ({
    tone: 'success',
    title: 'Connection added',
    message: `${name} was added. Validate it before deploying.`,
  }),
  providerConnected: (name: string): Message => ({
    tone: 'success',
    title: 'Provider connected',
    message: `${name} was verified and is ready for deployments.`,
  }),
  providerValidationStarted: (name: string): Message => ({
    tone: 'info',
    title: 'Validation started',
    message: `CloudBridge is checking access to ${name}. The status updates here when it finishes.`,
  }),
  providerValidationFailed: (name: string): Message => ({
    tone: 'error',
    title: 'Provider validation failed',
    message: `${name} could not be verified. Review the checks below.`,
  }),
  providerDisconnected: (name: string): Message => ({
    tone: 'success',
    title: 'Provider disconnected',
    message: `${name} was removed.`,
  }),
  profileSaved: (): Message => ({ tone: 'success', title: 'Settings updated', message: 'Your profile and preferences were saved.' }),
  organizationSaved: (): Message => ({ tone: 'success', title: 'Settings updated', message: 'The organization name was saved.' }),
  notificationsSaved: (): Message => ({ tone: 'success', title: 'Settings updated', message: 'Notification preferences were saved.' }),
  providerDisconnectAccepted: (name: string): Message => ({
    tone: 'info',
    title: 'Disconnection requested',
    message: `${name} is being removed.`,
  }),
  permissionDenied: (): Message => ({
    tone: 'error',
    title: 'Permission denied',
    message: 'You do not have permission to perform this action.',
  }),
  sessionExpired: (): Message => ({ tone: 'warning', title: 'Session expired', message: 'Sign in again to continue.' }),
  networkUnavailable: (): Message => ({
    tone: 'warning',
    title: 'Network unavailable',
    message: 'You are offline. Changes cannot be saved until the connection returns.',
  }),
  operationTimedOut: (): Message => ({
    tone: 'warning',
    title: 'Operation timed out',
    message: 'The outcome is unknown. Check the current status before trying again.',
  }),
  operationStillProcessing: (): Message => ({
    tone: 'info',
    title: 'Operation still processing',
    message: 'Automatic status updates have stopped. Refresh to check the latest status.',
  }),
} as const;
