import { AlertOctagon, Lock, SearchX, ServerCrash, WifiOff } from 'lucide-react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { describeError, toApiError } from '../../services/api/errors';
import { Button } from '../forms/Button';
import { RequestId } from './Alert';
import './feedback.css';

interface ErrorStateProps {
  error: unknown;
  /** Overrides for the generic title/message, e.g. "Application not found". */
  title?: string;
  message?: string;
  onRetry?: () => void;
  /** Extra actions shown after the default recovery action. */
  actions?: ReactNode;
  compact?: boolean;
}

function ErrorIcon({ error, size }: { error: unknown; size: number }) {
  const e = toApiError(error);
  const props = { className: 'state-block__icon', size, 'aria-hidden': true } as const;
  if (e.kind === 'network') return <WifiOff {...props} />;
  if (e.status === 403) return <Lock {...props} />;
  if (e.status === 404) return <SearchX {...props} />;
  if ((e.status ?? 0) >= 500 || e.kind === 'timeout') return <ServerCrash {...props} />;
  return <AlertOctagon {...props} />;
}

/** Replaces content that could not be loaded. Never shows raw server output. */
export function ErrorState({ error, title, message, onRetry, actions, compact = false }: ErrorStateProps) {
  const navigate = useNavigate();
  const description = describeError(error);
  const apiError = toApiError(error);

  let primary: ReactNode = null;
  if ((description.recovery === 'retry' || description.recovery === 'check_status') && onRetry) {
    primary = (
      <Button variant="primary" onClick={onRetry}>
        Try again
      </Button>
    );
  } else if (description.recovery === 'sign_in') {
    primary = (
      <Button variant="primary" onClick={() => navigate('/login')}>
        Sign in
      </Button>
    );
  } else if (description.recovery === 'go_back') {
    primary = <Button onClick={() => navigate(-1)}>Go back</Button>;
  }

  return (
    <div className={`state-block state-block--error${compact ? ' state-block--compact' : ''}`} role="alert">
      <ErrorIcon error={error} size={compact ? 24 : 32} />
      <h2>{title ?? description.title}</h2>
      <p className="state-block__message">{message ?? description.message}</p>
      <RequestId id={apiError.requestId} />
      {(primary || actions) && (
        <div className="state-block__actions">
          {primary}
          {actions}
        </div>
      )}
    </div>
  );
}
