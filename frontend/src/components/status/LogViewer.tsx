import { FileText, RefreshCw } from 'lucide-react';
import type { Resource } from '../../hooks/useResource';
import type { LogPage } from '../../types/api';
import { redact } from '../../utils/redact';
import { CardSkeleton } from '../feedback/CardSkeleton';
import { EmptyState } from '../feedback/EmptyState';
import { ErrorState } from '../feedback/ErrorState';
import { Button } from '../forms/Button';
import { DateTime } from './RelativeTime';
import './status.css';

/** 404 or 501 from a logs endpoint means the backend does not offer logs, not that something broke. */
function logsUnsupported(resource: Resource<LogPage>): boolean {
  const status = resource.error?.status;
  return resource.status === 'error' && (status === 404 || status === 501);
}

interface LogViewerProps {
  logs: Resource<LogPage>;
  label: string;
  emptyMessage: string;
  unsupportedMessage: string;
}

/** Log lines as text, with anything that looks like a credential redacted before display. */
export function LogViewer({ logs, label, emptyMessage, unsupportedMessage }: LogViewerProps) {
  if (logs.status === 'loading') return <CardSkeleton lines={5} label={`Loading ${label.toLowerCase()}`} />;
  if (logsUnsupported(logs)) {
    return <EmptyState compact icon={FileText} title={`${label} are not available`} message={unsupportedMessage} />;
  }
  if (logs.status === 'error') return <ErrorState compact error={logs.error} onRetry={logs.reload} title={`${label} could not be loaded`} />;
  const entries = logs.data?.entries ?? [];
  return (
    <div className="stack">
      <div className="cluster">
        <Button size="sm" icon={<RefreshCw size={14} aria-hidden="true" />} onClick={logs.reload} loading={logs.isRefreshing} loadingLabel="Refreshing…">
          Refresh
        </Button>
        {logs.data?.truncated && <span className="text-muted text-small">Showing the most recent entries only.</span>}
      </div>
      {entries.length === 0 ? (
        <p className="text-muted">{emptyMessage}</p>
      ) : (
        <ol className="log-viewer" aria-label={label}>
          {entries.map((entry, i) => (
            <li key={`${entry.timestamp}-${i}`} className={`log-viewer__line log-viewer__line--${entry.stream}`}>
              <span className="log-viewer__time">
                <DateTime value={entry.timestamp} />
              </span>
              <span className="log-viewer__stream">{entry.stream}</span>
              <span className="log-viewer__message">{redact(entry.message)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
