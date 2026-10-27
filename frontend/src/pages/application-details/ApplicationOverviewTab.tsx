import { Rocket } from 'lucide-react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { CopyButton } from '../../components/forms/CopyButton';
import { Panel } from '../../components/layout/Panel';
import { ExternalLink } from '../../components/navigation/ExternalLink';
import { InlineStatus } from '../../components/status/InlineStatus';
import { DateTime, RelativeTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import { applicationStatus, deploymentStatus, healthStatus, isDeploymentActive } from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { useApi } from '../../hooks/useApi';
import { useResource } from '../../hooks/useResource';
import type { DeploymentSummary } from '../../types/api';
import { formatDuration } from '../../utils/format';
import { shortId } from '../../utils/id';
import { applicationTypeLabel, runtimeLabel } from '../../utils/labels';
import { providerLabel } from '../../utils/providers';
import { sourceText, useApplication } from './applicationContext';

export function ApplicationOverviewTab() {
  const api = useApi();
  const { application, resource } = useApplication();
  const deployments = useResource(
    `application:${application.id}:recent-deployments`,
    (signal) => api.deployments.listForApplication(application.id, { page: 1, page_size: 5 }, signal),
    { pollWhile: (d) => d.items.some((x) => isDeploymentActive(x.status)), pollIntervalMs: 4000, maxPollDurationMs: 20 * 60_000 },
  );

  return (
    <div className="stack">
      <section className="app-health panel" aria-label="Health and current revision">
        <dl className="app-health__list">
          <div className="app-health__item">
            <dt>Health</dt>
            <dd>
              <StatusBadge status={healthStatus[application.health]} />
            </dd>
          </div>
          <div className="app-health__item">
            <dt>Status</dt>
            <dd>
              <StatusBadge status={applicationStatus[application.status]} />
            </dd>
          </div>
          <div className="app-health__item">
            <dt>Current version</dt>
            <dd className="mono">{application.current_version ?? <span className="text-muted">Not serving traffic</span>}</dd>
          </div>
          <div className="app-health__item app-health__item--wide">
            <dt>Endpoint</dt>
            <dd>
              {application.service_url ? (
                <ExternalLink href={application.service_url} />
              ) : (
                <span className="text-muted">Available after the first successful deployment</span>
              )}
            </dd>
          </div>
        </dl>
        <div className="app-health__digest">
          <span className="text-muted text-small">Image digest</span>
          {application.image_digest ? (
            <span className="cluster">
              <code className="digest" title={application.image_digest}>
                {application.image_digest}
              </code>
              <CopyButton value={application.image_digest} label="Copy image digest" />
            </span>
          ) : (
            <span className="text-muted">No image is serving traffic yet</span>
          )}
        </div>
      </section>

      <div className="page-grid">
        <Panel title="Details" actions={<InlineStatus resource={resource} />}>
          <dl className="description-list">
            <dt>Type</dt>
            <dd>
              {applicationTypeLabel[application.application_type]} · {runtimeLabel[application.runtime]}
            </dd>
            <dt>Deployment target</dt>
            <dd>
              {providerLabel(application.provider)}, {application.region} ·{' '}
              <Link to={`/providers/${encodeURIComponent(application.connection_id)}`}>View connection</Link>
            </dd>
            <dt>Source</dt>
            <dd className="mono">{sourceText(application)}</dd>
            {application.source.type === 'repository' && (
              <>
                <dt>Build</dt>
                <dd className="mono">
                  {application.source.dockerfile_path ?? 'Dockerfile'} in {application.source.build_context ?? '.'}
                </dd>
              </>
            )}
            <dt>Created</dt>
            <dd>
              <DateTime value={application.created_at} />
              {application.created_by && <span className="text-muted"> by {application.created_by}</span>}
            </dd>
            <dt>Last updated</dt>
            <dd>
              <RelativeTime value={application.updated_at} />
            </dd>
            <dt>Application ID</dt>
            <dd className="mono">{application.id}</dd>
          </dl>
        </Panel>

        <Panel
          title="Recent deployments"
          flush
          actions={<Link to={`/applications/${encodeURIComponent(application.id)}/deployments`}>View all</Link>}
        >
          {deployments.status === 'loading' && <TableSkeleton columns={3} rows={3} label="Loading deployments" />}
          {deployments.status === 'error' && <ErrorState compact error={deployments.error} onRetry={deployments.reload} />}
          {deployments.status === 'success' &&
            (deployments.data?.items.length ? (
              <DataTable<DeploymentSummary>
                caption="Recent deployments of this application"
                rows={deployments.data.items}
                rowKey={(d) => d.id}
                busy={deployments.isRefreshing}
                columns={[
                  {
                    key: 'id',
                    header: 'Deployment',
                    render: (d) => (
                      <span>
                        <Link to={`/deployments/${encodeURIComponent(d.id)}`} className="mono" title={d.id}>
                          {shortId(d.id)}
                        </Link>
                        {d.version && <span className="data-table__secondary mono">{d.version}</span>}
                      </span>
                    ),
                  },
                  { key: 'status', header: 'Status', render: (d) => <StatusBadge status={deploymentStatus[d.status]} /> },
                  {
                    key: 'when',
                    header: 'Started',
                    render: (d) => (
                      <span>
                        <RelativeTime value={d.created_at} />
                        {d.finished_at && <span className="data-table__secondary">took {formatDuration(d.created_at, d.finished_at)}</span>}
                      </span>
                    ),
                  },
                ]}
              />
            ) : (
              <EmptyState compact icon={Rocket} title="Not deployed yet" message="Deploy this application to create its first revision on the provider." />
            ))}
        </Panel>
      </div>
    </div>
  );
}
