import { Cloud, Plus, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button, ButtonLink } from '../../components/forms/Button';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { InlineStatus } from '../../components/status/InlineStatus';
import { RelativeTime } from '../../components/status/RelativeTime';
import { StatusBadge } from '../../components/status/StatusBadge';
import { connectionStatus } from '../../components/status/statusMaps';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource, type Resource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { describeError } from '../../services/api/errors';
import type { ProviderCapabilities, ProviderConnection, ProviderKind } from '../../types/api';
import { applicationTypeLabel } from '../../utils/labels';
import { messages, simulated } from '../../utils/messages';
import { PROVIDERS, PROVIDER_KINDS, accountLabel, accountRef } from '../../utils/providers';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';
import { ProviderPermissions } from './ProviderPermissions';
import './providers.css';

/** "3 of 3 checks passed" from the last permission validation the backend ran. */
function validationSummary(c: ProviderConnection): string {
  const checks = c.validation?.checks ?? [];
  if (checks.length === 0) return c.last_validated_at ? 'No check details reported' : 'Not validated yet';
  const passed = checks.filter((k) => k.status === 'passed').length;
  const failed = checks.filter((k) => k.status === 'failed').length;
  return failed > 0 ? `${failed} of ${checks.length} permission checks failed` : `${passed} of ${checks.length} permission checks passed`;
}

function CapabilitySummary({ caps }: { caps: Resource<ProviderCapabilities> }) {
  if (caps.status === 'loading') return <CardSkeleton lines={2} label="Loading capabilities" />;
  if (caps.status === 'error' || !caps.data) {
    return <p className="text-muted text-small">Capabilities are not available from the backend: {caps.error ? describeError(caps.error).title.toLowerCase() : 'unknown error'}.</p>;
  }
  const c = caps.data;
  return (
    <dl className="description-list capability-list">
      <dt>Application types</dt>
      <dd>{c.supported_application_types.map((t) => applicationTypeLabel[t]).join(', ')}</dd>
      <dt>CPU / memory</dt>
      <dd>
        {c.cpu_options.join(', ')} vCPU · {c.memory_options[0]}–{c.memory_options[c.memory_options.length - 1]}
      </dd>
      <dt>Scaling</dt>
      <dd>
        {c.min_instances_floor === 0 ? 'Scales to zero' : `At least ${c.min_instances_floor} instance`} · up to {c.max_instances_limit}{' '}
        instances
      </dd>
      <dt>Concurrency</dt>
      <dd>{c.supports_concurrency ? `Up to ${c.max_concurrency ?? 'unlimited'} requests per instance` : 'Not configurable'}</dd>
      <dt>Private services</dt>
      <dd>{c.supports_private_exposure ? 'Supported' : 'Not supported'}</dd>
      <dt>Secret references</dt>
      <dd>{c.supports_secret_references ? 'Supported' : 'Not supported'}</dd>
      <dt>Regions</dt>
      <dd>{c.regions.length} available</dd>
    </dl>
  );
}

function ProviderPanel({
  provider,
  connections,
  canManage,
  onChanged,
}: {
  provider: ProviderKind;
  connections: ProviderConnection[];
  canManage: boolean;
  onChanged(updated: ProviderConnection): void;
}) {
  const api = useApi();
  const demo = useDemoMode();
  const { notify } = useToast();
  const info = PROVIDERS[provider];
  const caps = useResource(`capabilities:${provider}`, (signal) => api.providers.getCapabilities(provider, signal));
  const [validatingId, setValidatingId] = useState<string | null>(null);
  const validate = useMutation((id: string) => api.providers.validate(id));

  const runValidation = async (c: ProviderConnection) => {
    setValidatingId(c.id);
    const result = await validate.run(c.id);
    setValidatingId(null);
    if (result.ok) {
      onChanged(result.data);
      notify(simulated(messages.providerValidationStarted(c.name), demo));
    }
  };

  const connectedCount = connections.filter((c) => c.status === 'connected').length;
  const headingId = `provider-${provider}`;

  return (
    <section className="panel provider-panel" aria-labelledby={headingId}>
      <header className="provider-panel__header">
        <div>
          <h2 id={headingId}>{info.label}</h2>
          <p className="text-muted text-small">
            {info.cloud} ·{' '}
            {connections.length === 0
              ? 'Not connected'
              : `${connectedCount} of ${connections.length} connection${connections.length === 1 ? '' : 's'} verified`}
          </p>
        </div>
        {canManage && (
          <ButtonLink size="sm" to={`/providers/new?provider=${provider}`} icon={<Plus size={14} aria-hidden="true" />}>
            Connect {info.shortLabel}
          </ButtonLink>
        )}
      </header>

      {validate.error && (
        <Alert tone="danger" title={describeError(validate.error).title} role="alert" requestId={validate.error.requestId}>
          {describeError(validate.error).message}
        </Alert>
      )}

      <div className="provider-panel__body">
        {connections.length === 0 ? (
          <EmptyState
            compact
            icon={Cloud}
            title={`No ${info.label} connection`}
            message={canManage ? `Connect a ${info.cloud} account to deploy to ${info.label}.` : 'Ask an administrator to connect one.'}
          />
        ) : (
          <ul className="connection-list">
            {connections.map((c) => (
              <li key={c.id} className="connection-list__item">
                <div className="connection-list__main">
                  <Link className="data-table__primary" to={`/providers/${encodeURIComponent(c.id)}`}>
                    {c.name}
                  </Link>
                  <StatusBadge status={connectionStatus[c.status]} />
                </div>
                <dl className="connection-list__facts">
                  <div>
                    <dt>{accountLabel(provider)}</dt>
                    <dd className="mono">{accountRef(c)}</dd>
                  </div>
                  <div>
                    <dt>Region</dt>
                    <dd>{c.default_region}</dd>
                  </div>
                  <div>
                    <dt>Last validated</dt>
                    <dd>{c.last_validated_at ? <RelativeTime value={c.last_validated_at} /> : 'Never'}</dd>
                  </div>
                  <div>
                    <dt>Permissions</dt>
                    <dd>{validationSummary(c)}</dd>
                  </div>
                </dl>
                {canManage && (
                  <div className="cluster">
                    <Button
                      size="sm"
                      icon={<ShieldCheck size={14} aria-hidden="true" />}
                      onClick={() => runValidation(c)}
                      loading={validatingId === c.id}
                      loadingLabel="Starting…"
                      disabled={c.status === 'validating' || validatingId !== null}
                    >
                      {c.status === 'validating' ? 'Validating…' : 'Validate'}
                    </Button>
                    <ButtonLink size="sm" variant="ghost" to={`/providers/${encodeURIComponent(c.id)}`}>
                      Details and disconnect
                    </ButtonLink>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="provider-panel__aside">
          <h3 className="text-small">Supported capabilities</h3>
          <CapabilitySummary caps={caps} />
          <details className="provider-panel__details">
            <summary>Required permissions</summary>
            <ProviderPermissions provider={provider} />
          </details>
        </div>
      </div>
    </section>
  );
}

export function ProvidersPage() {
  const api = useApi();
  const { can } = useAuth();
  const list = useResource('providers', (signal) => api.providers.list(signal), {
    pollWhile: (items) => items.some((c) => c.status === 'validating'),
    pollIntervalMs: 3000,
    maxPollDurationMs: 5 * 60_000,
  });

  if (list.status === 'error' && list.error?.status === 403) {
    return <AccessDeniedPage resource="cloud provider connections" requestId={list.error.requestId} />;
  }

  const canManage = can('providers:manage');
  const replace = (updated: ProviderConnection) => {
    list.setData((items) => items?.map((c) => (c.id === updated.id ? updated : c)));
    list.resumePolling();
  };

  return (
    <div className="page">
      <PageHeader
        title="Cloud providers"
        description="Connections CloudBridge uses to deploy to your cloud accounts. CloudBridge uses role assumption and workload identity with short-lived credentials, never long-lived access keys."
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Cloud providers' }]}
        actions={
          canManage ? (
            <ButtonLink variant="primary" to="/providers/new" icon={<Plus size={16} aria-hidden="true" />}>
              Connect provider
            </ButtonLink>
          ) : undefined
        }
      />
      {list.status === 'success' && (
        <div className="toolbar">
          <span className="toolbar__spacer" />
          <InlineStatus resource={list} />
        </div>
      )}
      {list.refreshError && list.data && (
        <Alert tone="warning" title="Could not refresh connections" requestId={list.refreshError.requestId} role="status">
          The statuses below may be out of date.
        </Alert>
      )}
      {list.status === 'loading' && (
        <Panel>
          <CardSkeleton lines={5} label="Loading provider connections" />
        </Panel>
      )}
      {list.status === 'error' && (
        <Panel>
          <ErrorState error={list.error} onRetry={list.reload} />
        </Panel>
      )}
      {list.status === 'success' &&
        list.data &&
        PROVIDER_KINDS.map((p) => (
          <ProviderPanel
            key={p}
            provider={p}
            connections={list.data!.filter((c) => c.provider === p)}
            canManage={canManage}
            onChanged={replace}
          />
        ))}
      <p className="text-muted text-small">Azure Container Apps is not supported yet.</p>
    </div>
  );
}
