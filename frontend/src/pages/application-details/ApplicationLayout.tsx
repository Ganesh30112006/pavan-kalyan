import { Rocket, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import { Link, Outlet, useNavigate, useParams } from 'react-router-dom';
import { ConfirmDialog } from '../../components/dialogs/ConfirmDialog';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button, ButtonLink } from '../../components/forms/Button';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { SubNav } from '../../components/navigation/SubNav';
import { StatusBadge } from '../../components/status/StatusBadge';
import { healthStatus, isDeploymentActive } from '../../components/status/statusMaps';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { describeError, type ApiError } from '../../services/api/errors';
import { randomId } from '../../utils/id';
import { messages, simulated } from '../../utils/messages';
import { providerLabel } from '../../utils/providers';
import { ApplicationNotFound } from '../applications/ApplicationsPage';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';
import { sourceText, type ApplicationContext } from './applicationContext';
import './application-details.css';

function isUncertain(error: ApiError): boolean {
  return error.kind === 'timeout' || error.kind === 'network' || (error.status ?? 0) >= 500;
}

export function ApplicationLayout() {
  const { applicationId = '' } = useParams();
  const api = useApi();
  const demo = useDemoMode();
  const { can } = useAuth();
  const { notify } = useToast();
  const navigate = useNavigate();

  const app = useResource(`application:${applicationId}`, (signal) => api.applications.get(applicationId, signal), {
    pollWhile: (a) => a.status === 'deploying' || a.status === 'deleting',
    pollIntervalMs: 4000,
    maxPollDurationMs: 20 * 60_000,
  });

  const [dialog, setDialog] = useState<'deploy' | 'delete' | null>(null);
  const deployKey = useRef('');
  const deploy = useMutation(() => api.deployments.create(applicationId, { idempotencyKey: deployKey.current }));
  const remove = useMutation(() => api.applications.remove(applicationId));
  const [uncertain, setUncertain] = useState<{ action: 'deploy' | 'delete'; error: ApiError } | null>(null);
  const [verifyingDelete, setVerifyingDelete] = useState(false);

  const crumbs = (last: string) => [
    { label: 'Overview', to: '/' },
    { label: 'Applications', to: '/applications' },
    { label: last },
  ];

  if (app.status === 'loading') {
    return (
      <div className="page">
        <PageHeader title="Application" breadcrumbs={crumbs('Loading…')} />
        <Panel>
          <CardSkeleton lines={5} label="Loading application" />
        </Panel>
      </div>
    );
  }
  if (verifyingDelete && (app.refreshError?.status === 404 || app.error?.status === 404)) {
    return (
      <div className="page">
        <PageHeader title="Application deleted" breadcrumbs={crumbs('Deleted')} />
        <Alert tone="success" title="The application was deleted" role="status" actions={<ButtonLink size="sm" to="/applications">Back to applications</ButtonLink>}>
          CloudBridge confirmed that this application no longer exists.
        </Alert>
      </div>
    );
  }
  if (app.status === 'error') {
    if (app.error?.status === 404) return <ApplicationNotFound />;
    if (app.error?.status === 403) return <AccessDeniedPage resource="this application" requestId={app.error.requestId} />;
    return (
      <div className="page">
        <PageHeader title="Application" breadcrumbs={crumbs('Error')} />
        <Panel>
          <ErrorState error={app.error} onRetry={app.reload} />
        </Panel>
      </div>
    );
  }

  const application = app.data!;
  const activeDeployment = application.latest_deployment && isDeploymentActive(application.latest_deployment.status);
  const deleting = application.status === 'deleting';
  const base = `/applications/${encodeURIComponent(application.id)}`;

  const openDeploy = () => {
    deploy.reset();
    deployKey.current = randomId();
    setDialog('deploy');
  };

  const confirmDeploy = async () => {
    const result = await deploy.run();
    if (result.ok) {
      setDialog(null);
      notify(simulated(messages.deploymentQueued(application.name), demo));
      navigate(`/deployments/${encodeURIComponent(result.data.id)}`);
    } else if (result.error && isUncertain(result.error)) {
      setDialog(null);
      setUncertain({ action: 'deploy', error: result.error });
    }
  };

  const confirmDelete = async () => {
    const result = await remove.run();
    if (result.ok) {
      setDialog(null);
      notify(
        simulated(
          result.data === 'accepted' ? messages.applicationDeletionAccepted(application.name) : messages.applicationDeleted(application.name),
          demo,
        ),
      );
      navigate('/applications', { replace: true });
    } else if (result.error && isUncertain(result.error)) {
      setDialog(null);
      setUncertain({ action: 'delete', error: result.error });
    }
  };

  /** After an uncertain result, look at the real state instead of repeating the request. */
  const checkStatus = () => {
    if (uncertain?.action === 'delete') setVerifyingDelete(true);
    setUncertain(null);
    app.reload();
  };

  const context: ApplicationContext = { application, resource: app };

  return (
    <div className="page">
      <PageHeader
        title={application.name}
        meta={<StatusBadge status={healthStatus[application.health]} />}
        description={application.description ?? undefined}
        breadcrumbs={crumbs(application.name)}
        actions={
          <>
            {can('deployments:create') && (
              <Button
                variant="primary"
                icon={<Rocket size={16} aria-hidden="true" />}
                onClick={openDeploy}
                disabled={Boolean(activeDeployment) || deleting}
                title={activeDeployment ? 'A deployment is already in progress' : undefined}
              >
                {application.current_version ? 'Redeploy' : 'Deploy'}
              </Button>
            )}
            {can('applications:delete') && (
              <Button
                variant="danger"
                icon={<Trash2 size={16} aria-hidden="true" />}
                onClick={() => {
                  remove.reset();
                  setDialog('delete');
                }}
                disabled={deleting}
              >
                Delete
              </Button>
            )}
          </>
        }
      />
      <SubNav
        label="Application sections"
        items={[
          { to: base, label: 'Overview', end: true },
          { to: `${base}/deployments`, label: 'Deployments' },
          { to: `${base}/configuration`, label: 'Configuration' },
          { to: `${base}/logs`, label: 'Logs' },
          { to: `${base}/metrics`, label: 'Metrics' },
          { to: `${base}/domains`, label: 'Domains' },
          { to: `${base}/automation`, label: 'Automation' },
        ]}
      />

      {uncertain && (
        <Alert
          tone="warning"
          title={uncertain.action === 'deploy' ? 'Deployment status unknown' : 'Deletion status unknown'}
          role="alert"
          requestId={uncertain.error.requestId}
          actions={<Button size="sm" onClick={checkStatus}>Check current status</Button>}
        >
          {describeError(uncertain.error).message}
        </Alert>
      )}
      {app.refreshError && (
        <Alert tone="warning" title="Could not refresh this application" requestId={app.refreshError.requestId} role="status">
          The details below may be out of date.
        </Alert>
      )}
      {activeDeployment && application.latest_deployment && (
        <Alert tone="info" title="Deployment in progress" role="status">
          <Link to={`/deployments/${encodeURIComponent(application.latest_deployment.id)}`}>View the deployment</Link> to follow its progress.
        </Alert>
      )}
      {app.polling === 'stopped' && (
        <Alert tone="warning" title={messages.operationStillProcessing().title} actions={<Button size="sm" onClick={app.resumePolling}>Resume updates</Button>}>
          {messages.operationStillProcessing().message}
        </Alert>
      )}

      <Outlet context={context} />

      <ConfirmDialog
        open={dialog === 'deploy'}
        title={`${application.current_version ? 'Redeploy' : 'Deploy'} ${application.name}?`}
        tone="primary"
        description="A new revision is built or pulled from the saved configuration and rolled out. The current revision keeps serving traffic until the new one passes verification."
        affected={
          <dl className="description-list">
            <dt>Source</dt>
            <dd className="mono">{sourceText(application)}</dd>
            <dt>Target</dt>
            <dd>
              {providerLabel(application.provider)}, {application.region}
            </dd>
          </dl>
        }
        confirmLabel="Start deployment"
        pendingLabel="Starting…"
        pending={deploy.isPending}
        error={deploy.error && !uncertain ? deploy.error : null}
        onConfirm={confirmDeploy}
        onCancel={() => setDialog(null)}
        onCheckStatus={() => {
          setDialog(null);
          checkStatus();
        }}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        title={`Delete ${application.name}?`}
        description={
          <p>
            This deletes the application and its configuration, and removes the service and its cloud resources from{' '}
            {providerLabel(application.provider)}. The service URL stops working. This cannot be undone.
          </p>
        }
        affected={
          <span>
            <strong>{application.name}</strong> · {providerLabel(application.provider)} · {application.region}
          </span>
        }
        confirmText={application.name}
        confirmLabel="Delete application"
        pendingLabel="Deleting…"
        pending={remove.isPending}
        error={remove.error && !uncertain ? remove.error : null}
        onConfirm={confirmDelete}
        onCancel={() => setDialog(null)}
        onCheckStatus={() => {
          setDialog(null);
          checkStatus();
        }}
      />
    </div>
  );
}
