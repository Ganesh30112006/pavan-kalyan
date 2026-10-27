import './feedback.css';

interface ProgressIndicatorProps {
  label: string;
  /** 0–100 when the backend reports real progress; omit for an indeterminate bar. */
  value?: number | null;
}

/**
 * A bar that never invents progress: without a reported value it is
 * indeterminate, and assistive technology hears no percentage.
 */
export function ProgressIndicator({ label, value }: ProgressIndicatorProps) {
  const determinate = typeof value === 'number' && value >= 0 && value <= 100;
  return (
    <div className="progress-indicator">
      <div className="progress-indicator__label">
        <span>{label}</span>
        {determinate && <span>{Math.round(value)}%</span>}
      </div>
      <div
        className="progress-bar"
        role="progressbar"
        aria-label={label}
        aria-valuemin={determinate ? 0 : undefined}
        aria-valuemax={determinate ? 100 : undefined}
        aria-valuenow={determinate ? Math.round(value) : undefined}
      >
        <div
          className={`progress-bar__fill${determinate ? '' : ' progress-bar__fill--indeterminate'}`}
          style={determinate ? { width: `${value}%` } : undefined}
        />
      </div>
    </div>
  );
}
