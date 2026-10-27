import type { NotificationEvent } from '../../types/api';

/** How each notification event is described to users (e-mail preferences and alert channels). */
export const EVENT_TEXT: Record<NotificationEvent, { label: string; description: string }> = {
  deployment_succeeded: { label: 'Deployment succeeded', description: 'A deployment you started finished successfully.' },
  deployment_failed: { label: 'Deployment failed', description: 'A deployment in your organization failed.' },
  rollback_completed: { label: 'Rollback completed', description: 'A rollback finished, successfully or not.' },
  provider_connection_failed: { label: 'Provider connection problem', description: 'A provider connection failed validation or lost access.' },
  application_deleted: { label: 'Application deleted', description: 'An application in your organization was deleted.' },
  application_unhealthy: { label: 'Application unhealthy', description: 'A running application started failing its health check.' },
  approval_requested: { label: 'Approval requested', description: 'A production deployment is waiting for an owner or admin to approve it.' },
};
