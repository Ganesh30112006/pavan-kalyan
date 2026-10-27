import { CheckCircle2, Circle, XCircle } from 'lucide-react';
import type { ConnectionStatus, ValidationStage } from '../../types/api';
import { Spinner } from '../feedback/InlineLoader';
import { ProgressIndicator } from '../feedback/ProgressIndicator';
import './status.css';

const STEPS: Array<{ stage: ValidationStage; label: string }> = [
  { stage: 'connecting', label: 'Connecting' },
  { stage: 'validating_permissions', label: 'Validating permissions' },
  { stage: 'checking_readiness', label: 'Checking readiness' },
];

const FAILED: readonly ConnectionStatus[] = [
  'invalid_credentials',
  'insufficient_permissions',
  'provider_unavailable',
  'timed_out',
  'error',
];

interface ConnectionProgressProps {
  status: ConnectionStatus;
  /** The stage the backend reports. Null means the backend does not report stages. */
  stage: ValidationStage | null;
  /** The stage that was active when validation ended, used to mark where it failed. */
  failedStage?: ValidationStage | null;
}

/**
 * Steps advance only from the stage the backend reports. Without a reported
 * stage the bar is indeterminate rather than showing invented progress.
 */
export function ConnectionProgress({ status, stage, failedStage = null }: ConnectionProgressProps) {
  if (status === 'validating' && stage === null) {
    return <ProgressIndicator label="Validating connection" />;
  }

  const failed = FAILED.includes(status);
  const connected = status === 'connected';
  const activeIndex = stage ? STEPS.findIndex((s) => s.stage === stage) : -1;
  const failedIndex = failed && failedStage ? STEPS.findIndex((s) => s.stage === failedStage) : -1;

  return (
    <ol className="steps" aria-label="Connection validation progress">
      {STEPS.map((step, index) => {
        let state: 'done' | 'active' | 'failed' | 'pending' = 'pending';
        if (connected) state = 'done';
        else if (status === 'validating' && activeIndex >= 0) {
          state = index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'pending';
        } else if (failed && failedIndex >= 0) {
          state = index < failedIndex ? 'done' : index === failedIndex ? 'failed' : 'pending';
        }
        const Icon = state === 'done' ? CheckCircle2 : state === 'failed' ? XCircle : null;
        const stateText = { done: 'completed', active: 'in progress', failed: 'failed', pending: 'not started' }[state];
        return (
          <li key={step.stage} className={`steps__item steps__item--${state}`} aria-current={state === 'active' ? 'step' : undefined}>
            <div className="steps__bar" aria-hidden="true" />
            <span className="steps__label">
              {Icon ? <Icon size={14} aria-hidden="true" /> : state === 'active' ? <Spinner /> : <Circle size={14} aria-hidden="true" />}
              {step.label}
              <span className="visually-hidden">: {stateText}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
