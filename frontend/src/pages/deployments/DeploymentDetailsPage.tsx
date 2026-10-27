import { AlertTriangle, Ban, CheckCircle2, Info, RotateCcw, SearchX, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ConfirmDialog } from '../../components/dialogs/ConfirmDialog';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { ErrorState } from '../../components/feedback/ErrorState';
import { OperationStatus, type OperationPhase } from '../../components/feedback/OperationStatus';
import { Button, ButtonLink } from '../../components/forms/Button';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { ExternalLink } from '../../components/navigation/ExternalLink';
import { DeploymentProgress } from '../../components/status/DeploymentProgress';
import { InlineStatus } from '../../components/status/InlineStatus';
import { LogViewer } from '../../components/status/LogViewer';
import { DateTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import { deploymentStatus, isDeploymentActive, stageLabel } from '../../components/status/statusMaps';
import { useAnnouncer } from '../../hooks/useAnnouncer';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import type { Deployment, DeploymentEvent, DeploymentStatus } from '../../types/api';
import { formatDuration } from '../../utils/format';
import { randomId, shortId } from '../../utils/id';
import { messages, simulated } from '../../utils/messages';
import { providerLabel } from '../../utils/providers';
import { redact } from '../../utils/redact';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';
import { ErrorPageLayout } from '../errors/ErrorPageLayout';
import './deployments.css';

/** The message for a status the page has just seen the deployment reach. */
function transitionMessage(d: Deployment, status: DeploymentStatus) {
  const rollback = d.kind === 'rollback';
  if (status === 'succeeded') return messages.deploymentSucceeded(d.application_name);
  if (status === 'rolled_back') return rollback ? messages.rollbackCompleted(d.application_name) : messages.deploymentFailed(d.application_name);
  if (status === 'failed' || status === 'failed_rollback') {
    return rollback || status === 'failed_rollback' ? messages.rollbackFailed(d.application_name) : messages.deploymentFailed(d.application_name);
  }
  if (status === 'cancelled') return messages.deploymentCancelled();
  return null;
}

const TRIGGER_TEXT: Record<Deployment['trigger'], string> = {
  manual: 'started in CloudBridge',
  push: 'GitHub push',
  pull_request: 'pull request preview',
  promotion: 'promotion',
  api: 'API or CLI',
};

function statusPanel(d: Deployment): { phase: OperationPhase; title: string; body: string } {
  const what = d.kind === 'rollback' ? 'Rollback' : 'Deployment';
  switch (d.status) {
    case 'awaiting_approval':
      return {
        phase: 'accepted',
        title: 'Waiting for approval',
        body: 'Production deployments of this application need an owner or admin to approve them before they start.',
      };
    case 'queued':
      return { phase: 'accepted', title: `${what} queued`, body: 'The request was accepted and is waiting for a deployment worker.' };
    case 'validating':
    case 'building':
    case 'publishing':
    case 'deploying':
    case 'verifying': {
      const active = d.stages.find((s) => s.status === 'active');
      return {
        phase: 'in_progress',
        title: `${what}: ${deploymentStatus[d.status].label.toLowerCase()}`,
        body: active ? `Current stage: ${stageLabel[active.name]}.` : 'The provider is processing the deployment.',
      };
    }
    case 'rolling_back':
      return { phase: 'in_progress', title: 'Rolling back', body: 'CloudBridge is restoring the previous revision.' };
    case 'succeeded':
      return { phase: 'succeeded', title: `${what} succeeded`, body: 'The new revision passed verification and is serving traffic.' };
    case 'rolled_back':
      return d.kind === 'rollback'
        ? { phase: 'succeeded', title: 'Rollback completed', body: 'The selected revision was restored and is serving traffic.' }
        : { phase: 'failed', title: 'Deployment rolled back', body: 'The new revision failed and the previous revision was restored.' };
    case 'failed':
      return { phase: 'failed', title: `${what} failed`, body: 'The previous revision, if any, keeps serving traffic.' };
    case 'failed_rollback':
      return {
        phase: 'failed',
        title: 'Rollback failed',
        body: 'The previous revision could not be restored. Check the application health and the events below.',
      };
    case 'cancelled':
      return { phase: 'cancelled', title: `${what} cancelled`, body: 'No further stages will run.' };
    default:
      return { phase: 'uncertain', title: 'Status unknown', body: 'CloudBridge reported a status this page does not recognise. Refresh to check again.' };
  }
}

const EVENT_ICON = { info: Info, warning: AlertTriangle, error: XCircle };

function EventTimeline({ events }: { events: DeploymentEvent[] }) {
  if (events.length === 0) return <p className="text-muted">No events have been recorded yet.</p>;
  const sorted = [...events].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  return (
    <ol className="timeline" aria-label="Deployment events">
      {sorted.map((e) => {
        const Icon = EVENT_ICON[e.level];
        return (
          <li key={e.id} className={`timeline__item timeline__item--${e.level}`}>
            <Icon size={14} aria-hidden="true" className="timeline__icon" />
            <span className="visually-hidden">{e.level}: </span>
            <span className="timeline__time">
              <DateTime value={e.timestamp} />
            </span>
            <span className="timeline__message">
              {e.stage && <span className="timeline__stage">{stageLabel[e.stage]}</span>}
              {redact(e.message)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function DeploymentDetailsPage() {
  const { deploymentId = '' } = useParams();
  const api = useApi();
  const demo = useDemoMode();
  const { can } = useAuth();
  const { notify } = useToast();
  const { announce } = useAnnouncer();
  const navigate = useNavigate();

  const deployment = useResource(`deployment:${deploymentId}`, (signal) => api.deployments.get(deploymentId, signal), {
    pollWhile: (d) => isDeploymentActive(d.status),
    pollIntervalMs: 3000,
    maxPollDurationMs: 30 * 60_000,
  });
  const active = deployment.data ? isDeploymentActive(deployment.data.status) : false;
  const events = useResource(`deployment:${deploymentId}:events`, (signal) => api.deployments.listEvents(deploymentId, signal), {
    pollWhile: () => active,
    pollIntervalMs: 5000,
    maxPollDurationMs: 30 * 60_000,
  });
  const logs = useResource(`deployment:${deploymentId}:logs`, (signal) => api.deployments.getLogs(deploymentId, signal), {
    pollWhile: () => active,
    pollIntervalMs: 5000,
    maxPollDurationMs: 30 * 60_000,
  });

  // Announce and notify only on a transition this page observed, never on first load.
  const previous = useRef<{ id: string; status: DeploymentStatus } | null>(null);
  const eventsRef = useRef(events);
  const logsRef = useRef(logs);
  useEffect(() => {
    eventsRef.current = events;
    logsRef.current = logs;
  });
  useEffect(() => {
    const d = deployment.data;
    if (!d) return;
    const before = previous.current?.id === d.id ? previous.current.status : null;
    previous.current = { id: d.id, status: d.status };
    if (before === null || before === d.status) return;
    const msg = transitionMessage(d, d.status);
    if (msg) {
      notify(simulated(msg, demo));
      announce(msg.title);
      eventsRef.current.reload();
      logsRef.current.reload();
    } else {
      announce(deploymentStatus[d.status].label);
    }
  }, [deployment.data, notify, announce, demo]);

  const [dialog, setDialog] = useState<'cancel' | 'rollback' | null>(null);
  const rollbackKey = useRef('');
  const cancel = useMutation(() => api.deployments.cancel(deploymentId));
  const rollback = useMutation(() => api.deployments.rollback(deploymentId, { idempotencyKey: rollbackKey.current }));
  const decide = useMutation((approve: boolean) =>
    approve ? api.deployments.approve(deploymentId) : api.deployments.reject(deploymentId),
  );

  if (deployment.status === 'loading') {
    return (
      <div className="page">
        <PageHeader title="Deployment" breadcrumbs={[{ label: 'Deployments', to: '/deployments' }, { label: 'Loading…' }]} />
        <Panel>
          <CardSkeleton lines={6} label="Loading deployment" />
        </Panel>
      </div>
    );
  }
  if (deployment.status === 'error') {
    if (deployment.error?.status === 404) {
      return (
        <ErrorPageLayout
          icon={SearchX}
          code="Error 404"
          title="Deployment not found"
          message="This deployment does not exist or you no longer have access to it."
          actions={<ButtonLink variant="primary" to="/deployments">Back to deployments</ButtonLink>}
        />
      );
    }
    if (deployment.error?.status === 403) return <AccessDeniedPage resource="this deployment" requestId={deployment.error.requestId} />;
    return (
      <div className="page">
        <PageHeader title="Deployment" breadcrumbs={[{ label: 'Deployments', to: '/deployments' }, { label: 'Error' }]} />
        <Panel>
          <ErrorState error={deployment.error} onRetry={deployment.reload} />
        </Panel>
      </div>
    );
  }

  const d = deployment.data!;
  const status = statusPanel(d);
  // Offered only when the backend permits the operation on this deployment and the user's role allows it.
  const canCancel = can('deployments:cancel') && d.allowed_actions.cancel;
  const canRollback = can('deployments:rollback') && d.allowed_actions.rollback;
  const canApprove = can('deployments:approve') && d.allowed_actions.approve;

  const onDecide = async (approve: boolean) => {
    const result = await decide.run(approve);
    if (result.ok) {
      deployment.setData(() => result.data);
      notify(simulated({ tone: approve ? 'success' : 'info', title: approve ? 'Deployment approved' : 'Deployment rejected' }, demo));
      deployment.resumePolling();
    }
  };

  const confirmCancel = async () => {
    const result = await cancel.run();
    if (result.ok) {
      setDialog(null);
      deployment.setData(() => result.data);
      notify(simulated(messages.deploymentCancelRequested(), demo));
      deployment.resumePolling();
    }
  };

  const confirmRollback = async () => {
    const result = await rollback.run();
    if (result.ok) {
      setDialog(null);
      notify(simulated(messages.rollbackStarted(d.application_name), demo));
      navigate(`/deployments/${encodeURIComponent(result.data.id)}`);
    }
  };

  return (
    <div className="page">
      <PageHeader
        title={`${d.kind === 'rollback' ? 'Rollback' : 'Deployment'} ${shortId(d.id)}`}
        meta={<StatusBadge status={deploymentStatus[d.status]} />}
        breadcrumbs={[
          { label: 'Overview', to: '/' },
          { label: 'Deployments', to: '/deployments' },
          { label: shortId(d.id) },
        ]}
        description={
          <>
            <Link to={`/applications/${encodeURIComponent(d.application_id)}`}>{d.application_name}</Link> on {providerLabel(d.provider)}
          </>
        }
        actions={
          <>
            {canApprove && (
              <>
                <Button variant="primary" icon={<CheckCircle2 size={16} aria-hidden="true" />} loading={decide.isPending} onClick={() => void onDecide(true)}>
                  Approve
                </Button>
                <Button icon={<XCircle size={16} aria-hidden="true" />} disabled={decide.isPending} onClick={() => void onDecide(false)}>
                  Reject
                </Button>
              </>
            )}
            {canCancel && (
              <Button variant="danger" icon={<Ban size={16} aria-hidden="true" />} onClick={() => { cancel.reset(); setDialog('cancel'); }}>
                Cancel deployment
              </Button>
            )}
            {canRollback && (
              <Button
                icon={<RotateCcw size={16} aria-hidden="true" />}
                onClick={() => {
                  rollback.reset();
                  rollbackKey.current = randomId();
                  setDialog('rollback');
                }}
              >
                Roll back to this revision
              </Button>
            )}
          </>
        }
      />

      <OperationStatus
        phase={deployment.polling === 'stopped' && active ? 'stale' : status.phase}
        title={deployment.polling === 'stopped' && active ? messages.operationStillProcessing().title : status.title}
        actions={
          deployment.polling === 'stopped' && active ? (
            <Button size="sm" onClick={deployment.resumePolling}>
              Resume updates
            </Button>
          ) : undefined
        }
      >
        {deployment.polling === 'stopped' && active ? messages.operationStillProcessing().message : status.body}
        {demo && <> This is a simulated deployment; no cloud resources are changed.</>}
      </OperationStatus>

      {decide.error && (
        <Alert tone="danger" title="The decision was not recorded" requestId={decide.error.requestId}>
          {decide.error.message}
        </Alert>
      )}
      {d.failure && (
        <Alert tone="danger" title="Failure reason">
          <p>{redact(d.failure.message)}</p>
          <p className="text-small text-muted">
            Error code: <code>{d.failure.code}</code>
          </p>
        </Alert>
      )}
      {deployment.refreshError && (
        <Alert tone="warning" title="Status updates are failing" requestId={deployment.refreshError.requestId} role="status">
          The status shown may be out of date. CloudBridge keeps retrying while the deployment is active.
        </Alert>
      )}

      <div className="page-grid">
        <Panel title="Progress" actions={<InlineStatus resource={deployment} />}>
          <DeploymentProgress stages={d.stages} progressPercent={d.progress_percent} />
        </Panel>
        <Panel title="Details">
          <dl className="description-list">
            <dt>Application</dt>
            <dd>
              <Link to={`/applications/${encodeURIComponent(d.application_id)}`}>{d.application_name}</Link>
            </dd>
            <dt>Type</dt>
            <dd>
              {d.kind === 'rollback' ? 'Rollback' : 'Deploy'}
              {d.rollback_of && (
                <>
                  {' '}
                  of <Link to={`/deployments/${encodeURIComponent(d.rollback_of)}`} className="mono">{shortId(d.rollback_of)}</Link>
                </>
              )}
            </dd>
            <dt>Target</dt>
            <dd>
              {providerLabel(d.provider)}
              {d.region && <>, {d.region}</>}
            </dd>
            <dt>Version</dt>
            <dd className="mono">{d.version ?? '—'}</dd>
            <dt>Image digest</dt>
            <dd className="mono">{d.image_digest ?? <span className="text-muted">Not built yet</span>}</dd>
            <dt>Source</dt>
            <dd className="mono">{d.source_ref ?? '—'}</dd>
            <dt>Triggered by</dt>
            <dd>
              {d.initiated_by ?? 'System'}
              {d.trigger !== 'manual' && <span className="text-muted"> ({TRIGGER_TEXT[d.trigger]})</span>}
            </dd>
            {d.approved_by && (
              <>
                <dt>Approved by</dt>
                <dd>{d.approved_by}</dd>
              </>
            )}
            <dt>Requested</dt>
            <dd>
              <DateTime value={d.created_at} />
            </dd>
            <dt>Finished</dt>
            <dd>{d.finished_at ? <DateTime value={d.finished_at} /> : <span className="text-muted">Not finished</span>}</dd>
            <dt>Duration</dt>
            <dd>{d.finished_at ? formatDuration(d.started_at ?? d.created_at, d.finished_at) : '—'}</dd>
            {d.service_url && (d.status === 'succeeded' || d.status === 'rolled_back') && (
              <>
                <dt>Endpoint</dt>
                <dd>
                  <ExternalLink href={d.service_url} />
                </dd>
              </>
            )}
            <dt>Deployment ID</dt>
            <dd className="mono">{d.id}</dd>
          </dl>
        </Panel>
      </div>

      <Panel title="Events" actions={events.status === 'success' ? <InlineStatus resource={events} /> : undefined}>
        {events.status === 'loading' && <CardSkeleton lines={4} label="Loading events" />}
        {events.status === 'error' && <ErrorState compact error={events.error} onRetry={events.reload} title="Events could not be loaded" />}
        {events.status === 'success' && events.data && <EventTimeline events={events.data} />}
      </Panel>

      <Panel title="Build and deployment logs">
        <LogViewer
          logs={logs}
          label="Deployment logs"
          emptyMessage={active ? 'No log output yet.' : 'No log output was recorded for this deployment.'}
          unsupportedMessage="The CloudBridge backend you are connected to does not provide deployment logs."
        />
      </Panel>

      <ConfirmDialog
        open={dialog === 'cancel'}
        title="Cancel this deployment?"
        description="The deployment stops at the next safe point. Stages already completed are not undone, and the previous revision keeps serving traffic."
        affected={
          <span>
            {d.application_name} · {providerLabel(d.provider)} · <span className="mono">{shortId(d.id)}</span>
          </span>
        }
        confirmLabel="Cancel deployment"
        pendingLabel="Requesting cancellation…"
        pending={cancel.isPending}
        error={cancel.error}
        onConfirm={confirmCancel}
        onCancel={() => setDialog(null)}
        onCheckStatus={() => {
          setDialog(null);
          deployment.reload();
        }}
      />
      <ConfirmDialog
        open={dialog === 'rollback'}
        title={`Roll back ${d.application_name}?`}
        tone="primary"
        description="A new deployment is started that restores this revision. The currently running revision is replaced once the rollback passes verification."
        affected={
          <dl className="description-list">
            <dt>Revision</dt>
            <dd className="mono">{d.source_ref ?? shortId(d.id)}</dd>
            <dt>Provider</dt>
            <dd>{providerLabel(d.provider)}</dd>
          </dl>
        }
        confirmLabel="Start rollback"
        pendingLabel="Starting rollback…"
        pending={rollback.isPending}
        error={rollback.error}
        onConfirm={confirmRollback}
        onCancel={() => setDialog(null)}
        onCheckStatus={() => {
          setDialog(null);
          navigate(`/deployments?application_id=${encodeURIComponent(d.application_id)}`);
        }}
      />
    </div>
  );
}
