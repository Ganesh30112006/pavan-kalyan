import { Spinner } from '../feedback/InlineLoader';
import type { StatusInfo } from './statusMaps';
import './status.css';

interface StatusBadgeProps {
  status: StatusInfo;
}

/** Colour plus text: the label always carries the meaning, colour only reinforces it. */
export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${status.tone}`}>
      {status.busy && <Spinner />}
      {status.label}
    </span>
  );
}
