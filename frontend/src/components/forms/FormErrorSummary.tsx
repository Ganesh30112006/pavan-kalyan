import { forwardRef } from 'react';
import { Alert } from '../feedback/Alert';
import { describeError, type ApiError } from '../../services/api/errors';
import { Button } from './Button';

interface FormErrorSummaryProps {
  /** The failed request, if the server rejected the submission. */
  error: ApiError | null;
  /** Field errors from client-side validation, keyed by field label. */
  fieldMessages?: string[];
  /**
   * Also list the server's field messages here. For forms that do not show every server field error next to
   * its field, so the reason for a rejection is never lost.
   */
  listServerFieldErrors?: boolean;
  onRetry?: () => void;
  onCheckStatus?: () => void;
}

/**
 * The single place a form reports a failed submission. Field-level messages
 * appear next to the fields; this summary explains what happened overall and
 * offers the right next step, so the same error is not also shown in a toast.
 */
export const FormErrorSummary = forwardRef<HTMLDivElement, FormErrorSummaryProps>(function FormErrorSummary(
  { error, fieldMessages = [], listServerFieldErrors = false, onRetry, onCheckStatus },
  ref,
) {
  const messages =
    listServerFieldErrors && error ? [...new Set([...fieldMessages, ...error.fieldErrors.map((f) => f.message)])] : fieldMessages;
  if (!error && messages.length === 0) return null;
  const description = error ? describeError(error) : null;
  const actions =
    description?.recovery === 'check_status' && onCheckStatus ? (
      <Button size="sm" onClick={onCheckStatus}>
        Check current status
      </Button>
    ) : description?.recovery === 'retry' && onRetry ? (
      <Button size="sm" onClick={onRetry}>
        Try again
      </Button>
    ) : undefined;

  return (
    <div ref={ref} tabIndex={-1} className="error-summary">
      <Alert
        tone="danger"
        role="alert"
        title={description?.title ?? 'Check the highlighted fields'}
        requestId={error?.requestId}
        actions={actions}
      >
        {description && <p>{description.message}</p>}
        {messages.length > 0 && (
          <ul>
            {messages.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
      </Alert>
    </div>
  );
});
