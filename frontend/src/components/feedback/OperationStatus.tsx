import type { ReactNode } from 'react';
import { Alert, type AlertTone } from './Alert';

export type OperationPhase =
  /** The backend accepted the request; the work has not finished. */
  | 'accepted'
  | 'in_progress'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  /** A request timed out or the connection dropped: the result is unknown. */
  | 'uncertain'
  /** Status updates stopped (time limit); the operation may still be running. */
  | 'stale';

const TONE: Record<OperationPhase, AlertTone> = {
  accepted: 'info',
  in_progress: 'info',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'warning',
  uncertain: 'warning',
  stale: 'warning',
};

interface OperationStatusProps {
  phase: OperationPhase;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  requestId?: string | null;
}

/**
 * Persistent, inline status for a long-running operation. It distinguishes
 * "accepted" from "completed" and says so plainly when the outcome is unknown.
 */
export function OperationStatus({ phase, title, children, actions, requestId }: OperationStatusProps) {
  return (
    <Alert tone={TONE[phase]} title={title} actions={actions} requestId={requestId} role={phase === 'failed' ? 'alert' : 'status'}>
      {children}
    </Alert>
  );
}
