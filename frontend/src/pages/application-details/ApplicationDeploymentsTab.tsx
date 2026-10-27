import { Rocket } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Alert } from '../../components/feedback/Alert';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { Panel } from '../../components/layout/Panel';
import { InlineStatus } from '../../components/status/InlineStatus';
import { RelativeTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import { deploymentStatus, isDeploymentActive } from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { Pagination } from '../../components/tables/Pagination';
import { useApi } from '../../hooks/useApi';
import { useListParams } from '../../hooks/usePaginationParams';
import { useResource } from '../../hooks/useResource';
import type { DeploymentSummary } from '../../types/api';
import { formatDuration } from '../../utils/format';
import { shortId } from '../../utils/id';
import { useApplication } from './applicationContext';

const NO_FILTERS = [] as const;

export function ApplicationDeploymentsTab() {
  const api = useApi();
  const { application } = useApplication();
  const { page, pageSize, update } = useListParams(NO_FILTERS);
  const list = useResource(
    `application:${application.id}:deployments:${page}:${pageSize}`,
    (signal) => api.deployments.listForApplication(application.id, { page, page_size: pageSize }, signal),
    { pollWhile: (d) => d.items.some((x) => isDeploymentActive(x.status)), pollIntervalMs: 4000, maxPollDurationMs: 15 * 60_000 },
  );

  return (
    <Panel title="Deployment history" flush actions={list.status === 'success' ? <InlineStatus resource={list} /> : undefined}>
      {list.refreshError && list.data && (
        <Alert tone="warning" title="Could not refresh deployments" requestId={list.refreshError.requestId} role="status">
          The list below may be out of date.
        </Alert>
      )}
      {list.status === 'loading' && <TableSkeleton columns={6} rows={5} label="Loading deployments" />}
      {list.status === 'error' && <ErrorState error={list.error} onRetry={list.reload} />}
      {list.status === 'success' && list.data?.items.length === 0 && (
        <EmptyState icon={Rocket} title="No deployments yet" message="Use Deploy above to create the first revision of this application." />
      )}
      {list.status === 'success' && list.data && list.data.items.length > 0 && (
        <>
          <DataTable<DeploymentSummary>
            caption={`Deployments of ${application.name}`}
            rows={list.data.items}
            rowKey={(d) => d.id}
            busy={list.isRefreshing}
            columns={[
              {
                key: 'id',
                header: 'Deployment',
                render: (d) => (
                  <Link to={`/deployments/${encodeURIComponent(d.id)}`} className="mono data-table__primary" title={d.id}>
                    {shortId(d.id)}
                  </Link>
                ),
              },
              { key: 'status', header: 'Status', render: (d) => <StatusBadge status={deploymentStatus[d.status]} /> },
              {
                key: 'version',
                header: 'Version',
                render: (d) => (
                  <span>
                    <span className="mono">{d.version ?? '—'}</span>
                    {d.image_digest && (
                      <span className="data-table__secondary mono" title={d.image_digest}>
                        {d.image_digest.slice(0, 19)}…
                      </span>
                    )}
                  </span>
                ),
              },
              { key: 'kind', header: 'Type', render: (d) => (d.kind === 'rollback' ? 'Rollback' : 'Deploy') },
              { key: 'by', header: 'Triggered by', render: (d) => d.initiated_by ?? <span className="text-muted">System</span> },
              {
                key: 'started',
                header: 'Started',
                className: 'data-table__nowrap',
                render: (d) => (
                  <span>
                    <RelativeTime value={d.created_at} />
                    {d.finished_at && <span className="data-table__secondary">{formatDuration(d.created_at, d.finished_at)}</span>}
                  </span>
                ),
              },
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
  );
}
