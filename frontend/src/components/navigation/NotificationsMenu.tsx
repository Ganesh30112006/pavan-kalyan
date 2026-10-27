import { Bell, CheckCircle2 } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../hooks/useApi';
import { useResource } from '../../hooks/useResource';
import { describeError } from '../../services/api/errors';
import { shortId } from '../../utils/id';
import { providerLabel } from '../../utils/providers';
import { CardSkeleton } from '../feedback/CardSkeleton';
import { Button } from '../forms/Button';
import { RelativeTime } from '../status/RelativeTime';
import { StatusBadge } from '../status/StatusBadge';
import { connectionStatus, deploymentStatus, FAILED_DEPLOYMENT_STATUSES } from '../status/statusMaps';
import type { ConnectionStatus } from '../../types/api';
import './navigation.css';

const CONNECTION_ATTENTION: readonly ConnectionStatus[] = [
  'invalid_credentials',
  'insufficient_permissions',
  'provider_unavailable',
  'timed_out',
  'error',
];

function NotificationsPanel({ id, onClose }: { id: string; onClose(): void }) {
  const api = useApi();
  const failures = useResource('notifications:failures', (signal) =>
    api.deployments.list(
      { page: 1, page_size: 5, status: [...FAILED_DEPLOYMENT_STATUSES], sort: { key: 'created_at', direction: 'desc' } },
      signal,
    ),
  );
  const connections = useResource('notifications:connections', (signal) => api.providers.list(signal));
  const attention = connections.data?.filter((c) => CONNECTION_ATTENTION.includes(c.status)) ?? [];
  const loading = failures.status === 'loading' || connections.status === 'loading';
  const empty = !loading && (failures.data?.items.length ?? 0) === 0 && attention.length === 0;
  const error = failures.error ?? connections.error;

  return (
    <div className="popover notifications" id={id} role="region" aria-label="Needs attention">
      <div className="popover__header">
        <strong>Needs attention</strong>
      </div>
      {loading && <CardSkeleton lines={3} label="Loading notifications" />}
      {error && <p className="popover__note">Some items could not be loaded: {describeError(error).title.toLowerCase()}.</p>}
      {empty && !error && (
        <p className="popover__empty">
          <CheckCircle2 size={16} aria-hidden="true" /> No failed deployments or connection problems.
        </p>
      )}
      <ul className="popover__list">
        {failures.data?.items.map((d) => (
          <li key={d.id}>
            <Link to={`/deployments/${encodeURIComponent(d.id)}`} className="popover__item" onClick={onClose}>
              <span>
                <span className="data-table__primary">{d.application_name}</span>
                <span className="data-table__secondary">
                  Deployment <span className="mono">{shortId(d.id)}</span> · <RelativeTime value={d.created_at} />
                </span>
              </span>
              <StatusBadge status={deploymentStatus[d.status]} />
            </Link>
          </li>
        ))}
        {attention.map((c) => (
          <li key={c.id}>
            <Link to={`/providers/${encodeURIComponent(c.id)}`} className="popover__item" onClick={onClose}>
              <span>
                <span className="data-table__primary">{c.name}</span>
                <span className="data-table__secondary">{providerLabel(c.provider)} connection</span>
              </span>
              <StatusBadge status={connectionStatus[c.status]} />
            </Link>
          </li>
        ))}
      </ul>
      <p className="popover__note">
        Live items from CloudBridge, refreshed each time this opens.{' '}
        <Link to="/settings/notifications" onClick={onClose}>
          Email preferences
        </Link>
      </p>
    </div>
  );
}

export function NotificationsMenu() {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="header-popover" ref={rootRef}>
      <Button
        ref={triggerRef}
        variant="ghost"
        iconOnly
        aria-label="Notifications"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <Bell size={18} aria-hidden="true" />
      </Button>
      {open && <NotificationsPanel id={panelId} onClose={() => setOpen(false)} />}
    </div>
  );
}
