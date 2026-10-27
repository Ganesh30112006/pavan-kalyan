import { Rocket, SearchX, X } from 'lucide-react';
import { useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Alert } from '../../components/feedback/Alert';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { Button, ButtonLink } from '../../components/forms/Button';
import { FilterSelect } from '../../components/forms/FilterSelect';
import { SearchInput } from '../../components/forms/SearchInput';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { InlineStatus } from '../../components/status/InlineStatus';
import { RelativeTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import {
  DEPLOYMENT_STATUS_GROUPS,
  deploymentStatus,
  isDeploymentActive,
  parseStatusFilter,
  type DeploymentStatusGroup,
} from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { Pagination } from '../../components/tables/Pagination';
import { useApi } from '../../hooks/useApi';
import { useSyncedSearch } from '../../hooks/useSyncedSearch';
import { parseSort, serializeSort, useListParams } from '../../hooks/usePaginationParams';
import { useResource } from '../../hooks/useResource';
import { DEPLOYMENT_STATUSES, PROVIDER_KINDS } from '../../services/api/decoders';
import type { DeploymentSortKey, DeploymentSummary, ListDeploymentsParams, ProviderKind } from '../../types/api';
import { formatDuration } from '../../utils/format';
import { shortId } from '../../utils/id';
import { providerLabel } from '../../utils/providers';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';

const FILTER_KEYS = ['q', 'status', 'provider', 'application_id', 'sort'] as const;
const SORT_KEYS: readonly DeploymentSortKey[] = ['created_at', 'application_name', 'status'];

export function DeploymentsPage() {
  const api = useApi();
  const { page, pageSize, filters, update } = useListParams(FILTER_KEYS);
  const pushSearch = useCallback((q: string) => update({ q }), [update]);
  const [searchText, setSearchText] = useSyncedSearch(filters.q, pushSearch);
  const status = parseStatusFilter(filters.status);
  const provider = (PROVIDER_KINDS as readonly string[]).includes(filters.provider) ? (filters.provider as ProviderKind) : undefined;
  const applicationId = filters.application_id || undefined;
  const sort = parseSort(filters.sort, SORT_KEYS);

  const query: ListDeploymentsParams = {
    page,
    page_size: pageSize,
    status,
    provider,
    application_id: applicationId,
    search: filters.q || undefined,
    sort,
  };

  const list = useResource(`deployments:${JSON.stringify(query)}`, (signal) => api.deployments.list(query, signal), {
    pollWhile: (d) => d.items.some((x) => isDeploymentActive(x.status)),
    pollIntervalMs: 4000,
    maxPollDurationMs: 15 * 60_000,
  });
  const app = useResource(`deployments:app:${applicationId ?? ''}`, (signal) => api.applications.get(applicationId ?? '', signal), {
    enabled: Boolean(applicationId),
  });

  if (list.status === 'error' && list.error?.status === 403) {
    return <AccessDeniedPage resource="deployments" requestId={list.error.requestId} />;
  }

  const filtering = Boolean(status || provider || applicationId || filters.q);
  const appName = app.data?.name ?? (app.status === 'error' ? 'an unknown application' : 'the selected application');
  const clearFilters = () => {
    setSearchText('');
    update({ q: null, status: null, provider: null, application_id: null });
  };

  return (
    <div className="page">
      <PageHeader
        title="Deployments"
        description="Every deployment and rollback across all applications and providers."
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Deployments' }]}
      />
      <div className="toolbar">
        <SearchInput label="Search deployments" placeholder="Search by application, ID or version" value={searchText} onChange={setSearchText} />
        <FilterSelect label="Status" value={status ? filters.status : ''} onChange={(v) => update({ status: v || null })}>
          <option value="">All statuses</option>
          {(Object.keys(DEPLOYMENT_STATUS_GROUPS) as DeploymentStatusGroup[]).map((g) => (
            <option key={g} value={g}>
              {DEPLOYMENT_STATUS_GROUPS[g].label}
            </option>
          ))}
          {DEPLOYMENT_STATUSES.filter((s) => s !== 'unknown').map((s) => (
            <option key={s} value={s}>
              {deploymentStatus[s].label}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect label="Provider" value={provider ?? ''} onChange={(v) => update({ provider: v || null })}>
          <option value="">All providers</option>
          {PROVIDER_KINDS.map((p) => (
            <option key={p} value={p}>
              {providerLabel(p)}
            </option>
          ))}
        </FilterSelect>
        {applicationId && (
          <span className="cluster">
            <span className="text-muted">
              Application: <strong>{appName}</strong>
            </span>
            <Button size="sm" variant="ghost" icon={<X size={14} aria-hidden="true" />} onClick={() => update({ application_id: null })}>
              Show all applications
            </Button>
          </span>
        )}
        <span className="toolbar__spacer" />
        {list.status === 'success' && <InlineStatus resource={list} />}
      </div>

      {list.refreshError && list.data && (
        <Alert tone="warning" title="Could not refresh deployments" requestId={list.refreshError.requestId} role="status">
          The list below may be out of date.
        </Alert>
      )}
      {list.polling === 'stopped' && (
        <Alert tone="warning" title="Automatic updates stopped" actions={<Button size="sm" onClick={list.resumePolling}>Resume updates</Button>}>
          Some deployments are still running. Resume updates or refresh to see their latest status.
        </Alert>
      )}

      <Panel flush>
        {list.status === 'loading' && <TableSkeleton columns={8} rows={6} label="Loading deployments" />}
        {list.status === 'error' && <ErrorState error={list.error} onRetry={list.reload} />}
        {list.status === 'success' && list.data?.items.length === 0 &&
          (filtering ? (
            <EmptyState
              icon={SearchX}
              title="No matching deployments"
              message="No deployments match the current search and filters."
              actions={<Button onClick={clearFilters}>Clear search and filters</Button>}
            />
          ) : (
            <EmptyState
              icon={Rocket}
              title="No deployments yet"
              message="Deploy an application to see its deployments here."
              actions={<ButtonLink to="/applications">Go to applications</ButtonLink>}
            />
          ))}
        {list.status === 'success' && list.data && list.data.items.length > 0 && (
          <>
            <DataTable<DeploymentSummary>
              caption="Deployments"
              rows={list.data.items}
              rowKey={(d) => d.id}
              busy={list.isRefreshing}
              sort={sort ?? null}
              onSortChange={(next) => update({ sort: serializeSort(next) })}
              columns={[
                {
                  key: 'id',
                  header: 'Deployment ID',
                  render: (d) => (
                    <span>
                      <Link to={`/deployments/${encodeURIComponent(d.id)}`} className="mono data-table__primary" title={d.id}>
                        {shortId(d.id)}
                      </Link>
                      {d.kind === 'rollback' && <span className="data-table__secondary">Rollback</span>}
                    </span>
                  ),
                },
                {
                  key: 'app',
                  header: 'Application',
                  sortKey: 'application_name',
                  render: (d) => <Link to={`/applications/${encodeURIComponent(d.application_id)}`}>{d.application_name}</Link>,
                },
                {
                  key: 'provider',
                  header: 'Provider',
                  render: (d) => (
                    <span>
                      {providerLabel(d.provider)}
                      {d.region && <span className="data-table__secondary">{d.region}</span>}
                    </span>
                  ),
                },
                {
                  key: 'version',
                  header: 'Version / digest',
                  render: (d) => (
                    <span>
                      <span className="mono">{d.version ?? d.source_ref ?? '—'}</span>
                      {d.image_digest && (
                        <span className="data-table__secondary mono" title={d.image_digest}>
                          {d.image_digest.slice(0, 19)}…
                        </span>
                      )}
                    </span>
                  ),
                },
                { key: 'status', header: 'Status', sortKey: 'status', render: (d) => <StatusBadge status={deploymentStatus[d.status]} /> },
                {
                  key: 'started',
                  header: 'Started',
                  sortKey: 'created_at',
                  className: 'data-table__nowrap',
                  render: (d) => <RelativeTime value={d.created_at} />,
                },
                {
                  key: 'duration',
                  header: 'Duration',
                  className: 'data-table__nowrap',
                  render: (d) =>
                    d.finished_at ? formatDuration(d.created_at, d.finished_at) : <span className="text-muted">{isDeploymentActive(d.status) ? 'Running' : '—'}</span>,
                },
                { key: 'by', header: 'Triggered by', render: (d) => d.initiated_by ?? <span className="text-muted">System</span> },
              ]}
            />
            <Pagination
              page={page}
              pageSize={pageSize}
              total={list.data.total}
              itemLabel="deployments"
              onPageChange={(p) => update({ page: p })}
              onPageSizeChange={(s) => update({ page_size: s })}
            />
          </>
        )}
      </Panel>
    </div>
  );
}
