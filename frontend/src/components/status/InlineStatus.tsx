import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Resource } from '../../hooks/useResource';
import { describeError } from '../../services/api/errors';
import { formatRelative } from '../../utils/format';
import { Spinner } from '../feedback/InlineLoader';
import { Button } from '../forms/Button';
import './status.css';

interface InlineStatusProps {
  resource: Pick<Resource<unknown>, 'isRefreshing' | 'refreshError' | 'updatedAt' | 'reload' | 'polling'>;
  /** Label of the refresh button. */
  refreshLabel?: string;
}

function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/**
 * Freshness of the data on screen: when it was loaded, whether a refresh is
 * running, and whether the last refresh failed (in which case the data shown
 * is stale). Includes the manual refresh control.
 */
export function InlineStatus({ resource, refreshLabel = 'Refresh' }: InlineStatusProps) {
  const now = useNow();
  const { isRefreshing, refreshError, updatedAt, reload, polling } = resource;
  const updatedIso = updatedAt ? new Date(updatedAt).toISOString() : null;
  return (
    <div className="cluster">
      {refreshError ? (
        <span className="inline-status inline-status--warning" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          Refresh failed ({describeError(refreshError).title.toLowerCase()}). Showing data from {formatRelative(updatedIso, now)}.
        </span>
      ) : isRefreshing ? (
        <span className="inline-status">
          <Spinner /> Refreshing…
        </span>
      ) : updatedIso ? (
        <span className="inline-status">
          Updated {formatRelative(updatedIso, now)}
          {polling === 'active' && ' · updating automatically'}
          {polling === 'paused' && ' · updates paused while this tab is hidden'}
        </span>
      ) : null}
      <Button size="sm" variant="ghost" icon={<RefreshCw size={14} aria-hidden="true" />} onClick={reload} disabled={isRefreshing}>
        {refreshLabel}
      </Button>
    </div>
  );
}
