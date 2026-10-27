import { Activity, SearchX } from 'lucide-react';
import { useCallback } from 'react';
import { RequirePermission } from '../../app/RequirePermission';
import { Alert } from '../../components/feedback/Alert';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { Button } from '../../components/forms/Button';
import { SearchInput } from '../../components/forms/SearchInput';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { InlineStatus } from '../../components/status/InlineStatus';
import { DateTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import { auditOutcome } from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { Pagination } from '../../components/tables/Pagination';
import { useApi } from '../../hooks/useApi';
import { useSyncedSearch } from '../../hooks/useSyncedSearch';
import { useListParams } from '../../hooks/usePaginationParams';
import { useResource } from '../../hooks/useResource';
import { AUDIT_OUTCOMES } from '../../services/api/decoders';
import type { AuditEvent, AuditOutcome } from '../../types/api';
import { humanize } from '../../utils/format';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';

const FILTER_KEYS = ['q', 'actor', 'action', 'outcome', 'resource_type', 'from', 'to'] as const;
const RESOURCE_TYPES = ['application', 'deployment', 'provider_connection', 'organization', 'user'] as const;
/** Actions the backend records. The filter sends the exact action name. */
const ACTIONS = [
  'application.created',
  'application.configuration_updated',
  'application.spec_validated',
  'application.deleted',
  'deployment.created',
  'deployment.rollback_started',
  'deployment.cancel_requested',
  'provider_connection.created',
  'provider_connection.validation_started',
  'provider_connection.validated',
  'provider_connection.deleted',
  'organization.updated',
] as const;

/** yyyy-mm-dd (local) → ISO start/end of that day. */
function dayBoundary(value: string, end: boolean): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const date = new Date(`${value}T${end ? '23:59:59.999' : '00:00:00'}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function AuditLog() {
  const api = useApi();
  const { page, pageSize, filters, update } = useListParams(FILTER_KEYS);
  const pushSearch = useCallback((q: string) => update({ q }), [update]);
  const [searchText, setSearchText] = useSyncedSearch(filters.q, pushSearch);
  const pushActor = useCallback((actor: string) => update({ actor }), [update]);
  const [actorText, setActorText] = useSyncedSearch(filters.actor, pushActor);
  const outcome = (AUDIT_OUTCOMES as readonly string[]).includes(filters.outcome) ? (filters.outcome as AuditOutcome) : undefined;
  const from = dayBoundary(filters.from, false);
  const to = dayBoundary(filters.to, true);
  const rangeError = from && to && from > to ? 'The start date must be on or before the end date.' : null;

  const query = {
    page,
    page_size: pageSize,
    search: filters.q || undefined,
    actor: filters.actor || undefined,
    action: filters.action || undefined,
    outcome,
    resource_type: filters.resource_type || undefined,
    from,
    to,
  };
  const events = useResource(`audit:${JSON.stringify(query)}`, (signal) => api.audit.list(query, signal), { enabled: !rangeError });
  const filtering = Boolean(filters.q || filters.actor || filters.action || outcome || filters.resource_type || from || to);

  if (events.status === 'error' && events.error?.status === 403) {
    return <AccessDeniedPage resource="the activity log" requestId={events.error.requestId} />;
  }

  return (
    <>
      <form className="toolbar" onSubmit={(e) => e.preventDefault()} aria-label="Filter activity">
        <SearchInput label="Search activity" placeholder="Search by action, resource or user" value={searchText} onChange={setSearchText} />
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-actor">
            Actor
          </label>
          <input
            id="audit-actor"
            className="input"
            type="search"
            placeholder="Name or email"
            autoComplete="off"
            value={actorText}
            onChange={(e) => setActorText(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-action">
            Action
          </label>
          <select id="audit-action" className="select" value={filters.action} onChange={(e) => update({ action: e.target.value || null })}>
            <option value="">All</option>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {humanize(a)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-outcome">
            Outcome
          </label>
          <select id="audit-outcome" className="select" value={outcome ?? ''} onChange={(e) => update({ outcome: e.target.value || null })}>
            <option value="">All</option>
            {AUDIT_OUTCOMES.filter((o) => o !== 'unknown').map((o) => (
              <option key={o} value={o}>
                {auditOutcome[o].label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-resource">
            Resource
          </label>
          <select id="audit-resource" className="select" value={filters.resource_type} onChange={(e) => update({ resource_type: e.target.value || null })}>
            <option value="">All</option>
            {RESOURCE_TYPES.map((r) => (
              <option key={r} value={r}>
                {humanize(r)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-from">
            From
          </label>
          <input id="audit-from" className="input" type="date" value={filters.from} onChange={(e) => update({ from: e.target.value || null })} aria-invalid={Boolean(rangeError) || undefined} />
        </div>
        <div className="field">
          <label className="field__label text-small" htmlFor="audit-to">
            To
          </label>
          <input id="audit-to" className="input" type="date" value={filters.to} onChange={(e) => update({ to: e.target.value || null })} aria-invalid={Boolean(rangeError) || undefined} />
        </div>
        <span className="toolbar__spacer" />
        {events.status === 'success' && <InlineStatus resource={events} />}
      </form>

      {rangeError && (
        <Alert tone="danger" role="alert">
          {rangeError}
        </Alert>
      )}
      {events.refreshError && events.data && (
        <Alert tone="warning" title="Could not refresh the activity log" requestId={events.refreshError.requestId} role="status">
          The entries below may be out of date.
        </Alert>
      )}

      {!rangeError && (
        <Panel flush>
          {events.status === 'loading' && <TableSkeleton columns={5} rows={8} label="Loading activity" />}
          {events.status === 'error' && <ErrorState error={events.error} onRetry={events.reload} />}
          {events.status === 'success' && events.data?.items.length === 0 &&
            (filtering ? (
              <EmptyState
                icon={SearchX}
                title="No matching activity"
                message="No entries match the current search and filters."
                actions={
                  <Button
                    onClick={() => {
                      setSearchText('');
                      setActorText('');
                      update({ q: null, actor: null, action: null, outcome: null, resource_type: null, from: null, to: null });
                    }}
                  >
                    Clear search and filters
                  </Button>
                }
              />
            ) : (
              <EmptyState icon={Activity} title="No activity recorded" message="Actions such as deployments and configuration changes are recorded here." />
            ))}
          {events.status === 'success' && events.data && events.data.items.length > 0 && (
            <>
              <DataTable<AuditEvent>
                caption="Activity log"
                rows={events.data.items}
                rowKey={(e) => e.id}
                busy={events.isRefreshing}
                columns={[
                  { key: 'time', header: 'Time', className: 'data-table__nowrap', render: (e) => <DateTime value={e.occurred_at} /> },
                  {
                    key: 'actor',
                    header: 'Actor',
                    render: (e) =>
                      e.actor ? (
                        <span>
                          {e.actor.display_name}
                          {e.actor.email && <span className="data-table__secondary">{e.actor.email}</span>}
                        </span>
                      ) : (
                        <span className="text-muted">System</span>
                      ),
                  },
                  { key: 'action', header: 'Action', render: (e) => humanize(e.action) },
                  {
                    key: 'resource',
                    header: 'Resource',
                    render: (e) => (
                      <span>
                        {e.resource_name ?? <span className="text-muted">—</span>}
                        <span className="data-table__secondary">{humanize(e.resource_type)}</span>
                      </span>
                    ),
                  },
                  { key: 'outcome', header: 'Outcome', render: (e) => <StatusBadge status={auditOutcome[e.outcome]} /> },
                  {
                    key: 'request',
                    header: 'Request ID',
                    render: (e) => (e.request_id ? <code className="text-small">{e.request_id}</code> : <span className="text-muted">—</span>),
                  },
                ]}
              />
              <Pagination
                page={page}
                pageSize={pageSize}
                total={events.data.total}
                itemLabel="entries"
                onPageChange={(p) => update({ page: p })}
                onPageSizeChange={(s) => update({ page_size: s })}
              />
            </>
          )}
        </Panel>
      )}
    </>
  );
}

export function AuditLogPage() {
  return (
    <div className="page">
      <PageHeader
        title="Activity log"
        description="Who did what, and whether it was allowed. This is the audit record of user and system actions; application output is under each application's Logs tab, and build progress under each deployment."
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Activity log' }]}
      />
      <RequirePermission permission="audit:read" resource="the activity log">
        <AuditLog />
      </RequirePermission>
    </div>
  );
}
