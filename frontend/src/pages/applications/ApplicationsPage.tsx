import { Boxes, Plus, SearchX } from 'lucide-react';
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
import { applicationStatus, deploymentStatus, healthStatus, isDeploymentActive } from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { Pagination } from '../../components/tables/Pagination';
import { useApi } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useSyncedSearch } from '../../hooks/useSyncedSearch';
import { parseSort, serializeSort, useListParams } from '../../hooks/usePaginationParams';
import { useResource } from '../../hooks/useResource';
import { APPLICATION_STATUSES, HEALTH_STATUSES, PROVIDER_KINDS } from '../../services/api/decoders';
import type {
  ApplicationSortKey,
  ApplicationStatus,
  ApplicationSummary,
  HealthStatus,
  ListApplicationsParams,
  ProviderKind,
} from '../../types/api';
import { applicationTypeLabel, runtimeLabel } from '../../utils/labels';
import { providerLabel } from '../../utils/providers';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';
import { ErrorPageLayout } from '../errors/ErrorPageLayout';

const FILTER_KEYS = ['q', 'status', 'health', 'provider', 'sort'] as const;
const SORT_KEYS: readonly ApplicationSortKey[] = ['name', 'updated_at', 'health'];

/** `health=unhealthy,degraded` in the URL → known values only. */
function parseHealth(value: string): HealthStatus[] {
  return value.split(',').filter((h): h is HealthStatus => (HEALTH_STATUSES as readonly string[]).includes(h));
}

export function ApplicationsPage() {
  const api = useApi();
  const { can } = useAuth();
  const { page, pageSize, filters, update } = useListParams(FILTER_KEYS);
  const pushSearch = useCallback((q: string) => update({ q }), [update]);
  const [searchText, setSearchText] = useSyncedSearch(filters.q, pushSearch);
  const status = (APPLICATION_STATUSES as readonly string[]).includes(filters.status)
    ? (filters.status as ApplicationStatus)
    : undefined;
  const health = parseHealth(filters.health);
  const provider = (PROVIDER_KINDS as readonly string[]).includes(filters.provider) ? (filters.provider as ProviderKind) : undefined;
  const sort = parseSort(filters.sort, SORT_KEYS);

  const query: ListApplicationsParams = {
    page,
    page_size: pageSize,
    search: filters.q || undefined,
    status,
    health: health.length ? health : undefined,
    provider,
    sort,
  };
  const apps = useResource(
    `applications:${JSON.stringify(query)}`,
    (signal) => api.applications.list(query, signal),
    {
      pollWhile: (data) => data.items.some((a) => a.status === 'deploying' || a.status === 'deleting'),
      pollIntervalMs: 5000,
      maxPollDurationMs: 10 * 60_000,
    },
  );

  const filtering = Boolean(filters.q || status || health.length || provider);
  const clearFilters = () => {
    setSearchText('');
    update({ q: null, status: null, health: null, provider: null });
  };

  if (apps.status === 'error' && apps.error?.status === 403) {
    return <AccessDeniedPage resource="applications" requestId={apps.error.requestId} />;
  }

  const createButton = can('applications:create') ? (
    <ButtonLink variant="primary" to="/applications/new" icon={<Plus size={16} aria-hidden="true" />}>
      Create application
    </ButtonLink>
  ) : undefined;

  return (
    <div className="page">
      <PageHeader
        title="Applications"
        description="Containerized applications managed by CloudBridge, on any connected provider."
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Applications' }]}
        actions={createButton}
      />

      <div className="toolbar">
        <SearchInput label="Search applications" placeholder="Search by name or description" value={searchText} onChange={setSearchText} />
        <FilterSelect label="Health" value={filters.health} onChange={(v) => update({ health: v || null })}>
          <option value="">All health</option>
          <option value="unhealthy,degraded">Needs attention</option>
          {HEALTH_STATUSES.map((h) => (
            <option key={h} value={h}>
              {healthStatus[h].label}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect label="Status" value={status ?? ''} onChange={(v) => update({ status: v || null })}>
          <option value="">All statuses</option>
          {APPLICATION_STATUSES.filter((s) => s !== 'unknown').map((s) => (
            <option key={s} value={s}>
              {applicationStatus[s].label}
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
        <span className="toolbar__spacer" />
        {apps.status === 'success' && <InlineStatus resource={apps} />}
      </div>

      {apps.refreshError && apps.data && (
        <Alert tone="warning" title="Could not refresh applications" requestId={apps.refreshError.requestId} role="status">
          The list below may be out of date.
        </Alert>
      )}

      <Panel flush>
        {apps.status === 'loading' && <TableSkeleton columns={6} rows={6} label="Loading applications" />}
        {apps.status === 'error' && <ErrorState error={apps.error} onRetry={apps.reload} />}
        {apps.status === 'success' && apps.data && apps.data.items.length === 0 && !filtering && apps.data.total === 0 && (
          <EmptyState
            icon={Boxes}
            title="No applications yet"
            message={
              can('applications:create')
                ? 'Create an application to deploy a container image or repository to a connected provider.'
                : 'No applications exist in this organization yet. Ask a developer or administrator to create one.'
            }
            actions={createButton}
          />
        )}
        {apps.status === 'success' && apps.data && apps.data.items.length === 0 && filtering && (
          <EmptyState
            icon={SearchX}
            title="No matching applications"
            message={filters.q ? `Nothing matched “${filters.q}” with the selected filters.` : 'No applications match the selected filters.'}
            actions={<Button onClick={clearFilters}>Clear search and filters</Button>}
          />
        )}
        {apps.status === 'success' && apps.data && apps.data.items.length === 0 && !filtering && apps.data.total > 0 && (
          <EmptyState
            icon={SearchX}
            title="This page is empty"
            message="There are no applications on this page. The list may have changed."
            actions={<Button onClick={() => update({ page: 1 })}>Go to first page</Button>}
          />
        )}
        {apps.status === 'success' && apps.data && apps.data.items.length > 0 && (
          <>
            <DataTable<ApplicationSummary>
              caption="Applications"
              rows={apps.data.items}
              rowKey={(a) => a.id}
              busy={apps.isRefreshing}
              sort={sort ?? null}
              onSortChange={(next) => update({ sort: serializeSort(next) })}
              columns={[
                {
                  key: 'name',
                  header: 'Name',
                  sortKey: 'name',
                  render: (a) => (
                    <span>
                      <Link className="data-table__primary" to={`/applications/${encodeURIComponent(a.id)}`}>
                        {a.name}
                      </Link>
                      {a.description && <span className="data-table__secondary">{a.description}</span>}
                    </span>
                  ),
                },
                {
                  key: 'type',
                  header: 'Type',
                  render: (a) => (
                    <span>
                      {applicationTypeLabel[a.application_type]}
                      <span className="data-table__secondary">{runtimeLabel[a.runtime]}</span>
                    </span>
                  ),
                },
                {
                  key: 'target',
                  header: 'Deployment target',
                  render: (a) => (
                    <span>
                      {providerLabel(a.provider)}
                      <span className="data-table__secondary">{a.region}</span>
                    </span>
                  ),
                },
                {
                  key: 'health',
                  header: 'Health',
                  sortKey: 'health',
                  render: (a) => (
                    <span>
                      <StatusBadge status={healthStatus[a.health]} />
                      <span className="data-table__secondary">{applicationStatus[a.status].label}</span>
                    </span>
                  ),
                },
                {
                  key: 'version',
                  header: 'Latest version',
                  render: (a) => (
                    <span>
                      {a.current_version ? <span className="mono">{a.current_version}</span> : <span className="text-muted">Not serving</span>}
                      {a.latest_deployment ? (
                        <span className="data-table__secondary cluster">
                          <Link to={`/deployments/${encodeURIComponent(a.latest_deployment.id)}`}>
                            <StatusBadge status={deploymentStatus[a.latest_deployment.status]} />
                          </Link>
                          {!isDeploymentActive(a.latest_deployment.status) && <RelativeTime value={a.latest_deployment.created_at} />}
                        </span>
                      ) : (
                        <span className="data-table__secondary">Never deployed</span>
                      )}
                    </span>
                  ),
                },
                {
                  key: 'updated',
                  header: 'Updated',
                  sortKey: 'updated_at',
                  className: 'data-table__nowrap',
                  render: (a) => <RelativeTime value={a.updated_at} />,
                },
              ]}
            />
            <Pagination
              page={page}
              pageSize={pageSize}
              total={apps.data.total}
              itemLabel="applications"
              onPageChange={(p) => update({ page: p })}
              onPageSizeChange={(s) => update({ page_size: s })}
            />
          </>
        )}
      </Panel>
    </div>
  );
}

/** Used by detail pages when the application ID does not exist. */
export function ApplicationNotFound() {
  return (
    <ErrorPageLayout
      icon={SearchX}
      code="Error 404"
      title="Application not found"
      message="This application does not exist or has been deleted."
      actions={<ButtonLink variant="primary" to="/applications">Back to applications</ButtonLink>}
    />
  );
}
