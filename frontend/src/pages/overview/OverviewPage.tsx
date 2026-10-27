import {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  CheckCircle2,
  Circle,
  Cloud,
  HeartPulse,
  Loader,
  Plus,
  Rocket,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { DeploymentTrendChart } from '../../components/charts/DeploymentTrendChart';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { ButtonLink } from '../../components/forms/Button';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { InlineStatus } from '../../components/status/InlineStatus';
import { RelativeTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import {
  ACTIVE_DEPLOYMENT_STATUSES,
  FAILED_DEPLOYMENT_STATUSES,
  auditOutcome,
  connectionStatus,
  deploymentStatus,
  healthStatus,
  isDeploymentActive,
} from '../../components/status/statusMaps';
import { DataTable } from '../../components/tables/DataTable';
import { useApi } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useResource, type Resource } from '../../hooks/useResource';
import type { DeploymentSummary, Paginated } from '../../types/api';
import { deploymentTrend } from '../../utils/deploymentTrend';
import { humanize } from '../../utils/format';
import { providerLabel } from '../../utils/providers';
import './overview.css';

const TREND_DAYS = 14;
const TREND_SAMPLE = 50;

function GettingStarted({ hasConnection, hasApplication, hasDeployment }: { hasConnection: boolean; hasApplication: boolean; hasDeployment: boolean }) {
  const steps = [
    { done: hasConnection, label: 'Connect a cloud provider', to: '/providers/new' },
    { done: hasApplication, label: 'Create an application', to: '/applications/new' },
    { done: hasDeployment, label: 'Deploy the application', to: '/applications' },
  ];
  if (steps.every((s) => s.done)) return null;
  return (
    <Panel title="Get started">
      <ol className="checklist">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? 'checklist__item checklist__item--done' : 'checklist__item'}>
            {step.done ? <CheckCircle2 size={16} aria-hidden="true" /> : <Circle size={16} aria-hidden="true" />}
            {step.done ? (
              <span>
                {step.label} <span className="visually-hidden">(done)</span>
              </span>
            ) : (
              <Link to={step.to}>{step.label}</Link>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}

/** A count from a list endpoint's `total`, linking to the same filter on the full page. */
function Metric({ label, icon: Icon, to, resource, tone }: { label: string; icon: LucideIcon; to: string; resource: Pick<Resource<{ total: number }>, 'status' | 'data'>; tone?: 'danger' | 'success' }) {
  return (
    <Link to={to} className={`metric${tone ? ` metric--${tone}` : ''}`}>
      <span className="metric__label">
        <Icon size={14} aria-hidden="true" /> {label}
      </span>
      <span className="metric__value">
        {resource.status === 'loading' && <span className="skeleton metric__skeleton" aria-label="Loading" />}
        {resource.status === 'error' && <span className="text-muted metric__unavailable">Unavailable</span>}
        {resource.status === 'success' && resource.data?.total.toLocaleString()}
      </span>
    </Link>
  );
}

function TrendPanel({ recent }: { recent: Resource<Paginated<DeploymentSummary>> }) {
  // Measured from when the data was fetched, so re-rendering never changes the result.
  const asOf = recent.updatedAt ?? 0;
  const days = recent.data ? deploymentTrend(recent.data.items, TREND_DAYS, new Date(asOf)) : [];
  const finished = days.reduce((n, d) => n + d.succeeded + d.failed, 0);
  const sampled = recent.data?.items ?? [];
  const oldest = sampled[sampled.length - 1];
  const truncated =
    recent.data !== undefined &&
    recent.data.total > sampled.length &&
    oldest !== undefined &&
    asOf - new Date(oldest.created_at).getTime() < TREND_DAYS * 86_400_000;

  return (
    <Panel title={`Deployment outcomes, last ${TREND_DAYS} days`}>
      {recent.status === 'loading' && <CardSkeleton lines={4} label="Loading deployment history" />}
      {recent.status === 'error' && <ErrorState compact error={recent.error} onRetry={recent.reload} />}
      {recent.status === 'success' &&
        (finished === 0 ? (
          <EmptyState compact icon={BarChart3} title="No finished deployments in this period" message="The chart appears once deployments have completed." />
        ) : (
          <div className="stack">
            <DeploymentTrendChart days={days} />
            <p className="text-muted text-small">
              Counted from the {sampled.length} most recent deployments returned by the API, by finish date.
              {truncated && ' Older deployments in this period are not included.'}
            </p>
          </div>
        ))}
    </Panel>
  );
}

export function OverviewPage() {
  const api = useApi();
  const { can, session } = useAuth();
  const canReadAudit = can('audit:read');
  const poll = { pollIntervalMs: 5000, maxPollDurationMs: 10 * 60_000 };

  const apps = useResource('overview:apps', (signal) => api.applications.list({ page: 1, page_size: 1 }, signal));
  const healthy = useResource('overview:healthy', (signal) => api.applications.list({ page: 1, page_size: 1, health: ['healthy'] }, signal));
  const attention = useResource('overview:attention', (signal) =>
    api.applications.list({ page: 1, page_size: 5, health: ['unhealthy', 'degraded'], sort: { key: 'health', direction: 'asc' } }, signal),
  );
  const active = useResource(
    'overview:active',
    (signal) => api.deployments.list({ page: 1, page_size: 1, status: [...ACTIVE_DEPLOYMENT_STATUSES] }, signal),
    { ...poll, pollWhile: (d) => d.total > 0 },
  );
  const failed = useResource('overview:failed', (signal) =>
    api.deployments.list({ page: 1, page_size: 1, status: [...FAILED_DEPLOYMENT_STATUSES] }, signal),
  );
  const recent = useResource(
    'overview:recent',
    (signal) => api.deployments.list({ page: 1, page_size: TREND_SAMPLE, sort: { key: 'created_at', direction: 'desc' } }, signal),
    { ...poll, pollWhile: (d) => d.items.some((x) => isDeploymentActive(x.status)) },
  );
  const connections = useResource('overview:connections', (signal) => api.providers.list(signal));
  const activity = useResource('overview:activity', (signal) => api.audit.list({ page: 1, page_size: 5 }, signal), {
    enabled: canReadAudit,
  });

  const all = [apps, healthy, attention, active, failed, recent, connections, ...(canReadAudit ? [activity] : [])];
  const reloadAll = () => all.forEach((r) => r.reload());
  const anyFailed = all.some((r) => r.status === 'error');
  const connected = connections.data?.filter((c) => c.status === 'connected').length ?? 0;

  return (
    <div className="page">
      <PageHeader
        title="Overview"
        description={`Applications, deployments and provider connections for ${session?.organization.name ?? 'your organization'}.`}
      />

      {anyFailed && (
        <p className="inline-status inline-status--warning" role="status">
          Some sections could not be loaded. The rest of the page is up to date.{' '}
          <button type="button" className="link-button" onClick={reloadAll}>
            Retry all
          </button>
        </p>
      )}

      <section className="metrics" aria-label="Summary">
        <Metric label="Total applications" icon={Boxes} to="/applications" resource={apps} />
        <Metric label="Healthy applications" icon={HeartPulse} to="/applications?health=healthy" resource={healthy} tone="success" />
        <Metric label="Active deployments" icon={Loader} to="/deployments?status=active" resource={active} />
        <Metric label="Failed deployments" icon={XCircle} to="/deployments?status=failed" resource={failed} tone="danger" />
      </section>

      {apps.status === 'success' && connections.status === 'success' && recent.status === 'success' && (
        <GettingStarted hasConnection={connected > 0} hasApplication={(apps.data?.total ?? 0) > 0} hasDeployment={(recent.data?.total ?? 0) > 0} />
      )}

      <div className="page-grid">
        <Panel
          title="Applications requiring attention"
          flush
          actions={<Link to="/applications?health=unhealthy,degraded">View all</Link>}
        >
          {attention.status === 'loading' && <CardSkeleton lines={3} label="Loading applications" />}
          {attention.status === 'error' && <ErrorState compact error={attention.error} onRetry={attention.reload} />}
          {attention.status === 'success' &&
            (attention.data?.items.length ? (
              <ul className="simple-list">
                {attention.data.items.map((a) => (
                  <li key={a.id} className="simple-list__item">
                    <span>
                      <Link to={`/applications/${encodeURIComponent(a.id)}`} className="data-table__primary">
                        {a.name}
                      </Link>
                      <span className="data-table__secondary">
                        {providerLabel(a.provider)} · {a.region}
                      </span>
                    </span>
                    <StatusBadge status={healthStatus[a.health]} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState compact icon={CheckCircle2} title="Nothing needs attention" message="No application is reported as unhealthy or degraded." />
            ))}
        </Panel>

        <TrendPanel recent={recent} />
      </div>

      <div className="page-grid">
        <Panel
          title="Recent deployment activity"
          flush
          actions={
            <>
              {recent.status === 'success' && <InlineStatus resource={recent} />}
              <Link to="/deployments">View all</Link>
            </>
          }
        >
          {recent.status === 'loading' && <TableSkeleton columns={4} rows={5} label="Loading deployments" />}
          {recent.status === 'error' && <ErrorState compact error={recent.error} onRetry={recent.reload} />}
          {recent.status === 'success' &&
            (recent.data?.items.length ? (
              <DataTable<DeploymentSummary>
                caption="Recent deployments"
                rows={recent.data.items.slice(0, 6)}
                rowKey={(d) => d.id}
                busy={recent.isRefreshing}
                columns={[
                  {
                    key: 'app',
                    header: 'Application',
                    render: (d) => (
                      <span>
                        <Link to={`/deployments/${encodeURIComponent(d.id)}`} className="data-table__primary">
                          {d.application_name}
                        </Link>
                        <span className="data-table__secondary">
                          {providerLabel(d.provider)}
                          {d.version && <> · {d.version}</>}
                        </span>
                      </span>
                    ),
                  },
                  { key: 'status', header: 'Status', render: (d) => <StatusBadge status={deploymentStatus[d.status]} /> },
                  { key: 'created', header: 'Started', render: (d) => <RelativeTime value={d.created_at} /> },
                ]}
              />
            ) : (
              <EmptyState compact icon={Rocket} title="No deployments yet" message="Deployments appear here once an application is deployed." />
            ))}
        </Panel>

        <Panel title="Connected cloud providers" flush actions={<Link to="/providers">Manage</Link>}>
          {connections.status === 'loading' && <CardSkeleton lines={3} label="Loading connections" />}
          {connections.status === 'error' && <ErrorState compact error={connections.error} onRetry={connections.reload} />}
          {connections.status === 'success' &&
            (connections.data?.length ? (
              <ul className="simple-list">
                {connections.data.map((c) => (
                  <li key={c.id} className="simple-list__item">
                    <span>
                      <Link to={`/providers/${encodeURIComponent(c.id)}`}>{c.name}</Link>
                      <span className="data-table__secondary">
                        {providerLabel(c.provider)} · {c.default_region}
                      </span>
                    </span>
                    <StatusBadge status={connectionStatus[c.status]} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                compact
                icon={Cloud}
                title="No providers connected"
                message="Connect AWS App Runner or Google Cloud Run to deploy applications."
              />
            ))}
        </Panel>
      </div>

      <div className="page-grid">
        <Panel title="Quick actions">
          <div className="quick-actions">
            {can('applications:create') && (
              <ButtonLink variant="primary" to="/applications/new" icon={<Plus size={16} aria-hidden="true" />}>
                Create application
              </ButtonLink>
            )}
            {can('providers:manage') && (
              <ButtonLink to="/providers/new" icon={<Cloud size={16} aria-hidden="true" />}>
                Connect a provider
              </ButtonLink>
            )}
            <ButtonLink to="/deployments" icon={<Rocket size={16} aria-hidden="true" />}>
              View deployments
            </ButtonLink>
            <ButtonLink to="/deployments?status=failed" icon={<AlertTriangle size={16} aria-hidden="true" />}>
              Review failures
            </ButtonLink>
          </div>
        </Panel>

        {canReadAudit && (
          <Panel title="Recent activity" flush actions={<Link to="/activity">View activity log</Link>}>
            {activity.status === 'loading' && <TableSkeleton columns={4} rows={5} label="Loading activity" />}
            {activity.status === 'error' && <ErrorState compact error={activity.error} onRetry={activity.reload} />}
            {activity.status === 'success' &&
              (activity.data?.items.length ? (
                <ul className="simple-list">
                  {activity.data.items.map((e) => (
                    <li key={e.id} className="simple-list__item">
                      <span>
                        <span className="data-table__primary">{humanize(e.action)}</span>
                        {e.resource_name && <span> · {e.resource_name}</span>}
                        <span className="data-table__secondary">
                          {e.actor?.display_name ?? 'System'} · <RelativeTime value={e.occurred_at} />
                        </span>
                      </span>
                      <StatusBadge status={auditOutcome[e.outcome]} />
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState compact icon={Activity} title="No activity yet" message="Actions taken in this organization appear here." />
              ))}
          </Panel>
        )}
      </div>
    </div>
  );
}
