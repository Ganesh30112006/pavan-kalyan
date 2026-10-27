import { Ban, Check, Minus, X } from 'lucide-react';
import type { DeploymentStage } from '../../types/api';
import { formatDuration } from '../../utils/format';
import { Spinner } from '../feedback/InlineLoader';
import { ProgressIndicator } from '../feedback/ProgressIndicator';
import { stageLabel, stageStatus } from './statusMaps';
import './status.css';

interface DeploymentProgressProps {
  stages: DeploymentStage[];
  /** Real progress from the provider, if any. Never estimated here. */
  progressPercent?: number | null;
}

function StageIcon({ status }: { status: DeploymentStage['status'] }) {
  switch (status) {
    case 'succeeded':
      return <Check size={14} aria-hidden="true" />;
    case 'failed':
      return <X size={14} aria-hidden="true" />;
    case 'active':
      return <Spinner />;
    case 'cancelled':
      return <Ban size={12} aria-hidden="true" />;
    case 'skipped':
      return <Minus size={12} aria-hidden="true" />;
    default:
      return null;
  }
}

/** Stages exactly as the backend reports them, each with a text status. */
export function DeploymentProgress({ stages, progressPercent = null }: DeploymentProgressProps) {
  if (stages.length === 0) {
    return <p className="text-muted">The backend has not reported any stages for this deployment yet.</p>;
  }
  const active = stages.find((s) => s.status === 'active');
  return (
    <div className="stack">
      {active && typeof progressPercent === 'number' && (
        <ProgressIndicator label={`${stageLabel[active.name]} progress`} value={progressPercent} />
      )}
      <ol className="stage-list" aria-label="Deployment stages">
        {stages.map((stage) => {
          const info = stageStatus[stage.status];
          return (
            <li
              key={stage.name}
              className={`stage-list__item stage-list__item--${stage.status}`}
              aria-current={stage.status === 'active' ? 'step' : undefined}
            >
              <span className="stage-list__icon">
                <StageIcon status={stage.status} />
              </span>
              <span>
                <span className="stage-list__name">{stageLabel[stage.name]}</span>
                <span className="text-muted text-small"> · {info.label}</span>
              </span>
              <span className="stage-list__meta">
                {stage.started_at && stage.finished_at ? formatDuration(stage.started_at, stage.finished_at) : ''}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
