import { ServerCrash } from 'lucide-react';
import { Button } from '../../components/forms/Button';
import { useAuth } from '../../hooks/useAuth';
import { describeError, type ApiError } from '../../services/api/errors';
import { ErrorPageLayout } from './ErrorPageLayout';

interface ServiceUnavailableProps {
  error?: ApiError | null;
  onRetry?: () => void;
}

/** Shown when CloudBridge cannot be reached at all. There is no offline sign-in. */
export function ServiceUnavailableContent({ error, onRetry }: ServiceUnavailableProps) {
  const description = error ? describeError(error) : null;
  return (
    <ErrorPageLayout
      icon={ServerCrash}
      code={error?.status ? `Error ${error.status}` : 'Service unavailable'}
      title="CloudBridge is unavailable"
      message={
        <>
          <p>{description?.message ?? 'The CloudBridge service could not be reached.'}</p>
          <p>Your applications keep running on their cloud providers; only the management console is affected.</p>
        </>
      }
      requestId={error?.requestId}
      standalone
      actions={
        <Button variant="primary" onClick={onRetry ?? (() => window.location.reload())}>
          Try again
        </Button>
      }
    />
  );
}

export function ServiceUnavailablePage() {
  const { bootstrapError, retry } = useAuth();
  return <ServiceUnavailableContent error={bootstrapError} onRetry={retry} />;
}
