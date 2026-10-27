import { Globe, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { CopyButton } from '../../components/forms/CopyButton';
import { TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { Panel } from '../../components/layout/Panel';
import { StatusBadge } from '../../components/status/StatusBadge';
import type { StatusInfo } from '../../components/status/statusMaps';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import type { CustomDomain, DomainStatus } from '../../types/api';
import { simulated } from '../../utils/messages';
import { providerLabel } from '../../utils/providers';
import { useApplication } from './applicationContext';
import { TableScroll } from '../../components/tables/TableScroll';
import { fieldErrorFor } from '../../services/api/errors';

const DOMAIN_STATUS: Record<DomainStatus, StatusInfo> = {
  pending_dns: { label: 'Waiting for DNS', tone: 'warning' },
  verifying: { label: 'Issuing certificate', tone: 'info', busy: true },
  active: { label: 'Active', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  removing: { label: 'Removing', tone: 'neutral', busy: true },
};

const DOMAIN = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

function DomainItem({ domain, appId, canEdit, onChange }: { domain: CustomDomain; appId: string; canEdit: boolean; onChange(): void }) {
  const api = useApi();
  const check = useMutation(() => api.platform.checkDomain(appId, domain.id));
  const remove = useMutation(() => api.platform.removeDomain(appId, domain.id));
  return (
    <li className="domain-item">
      <div className="domain-item__header">
        <h3 className="domain-item__name">{domain.domain}</h3>
        <div className="cluster">
          <StatusBadge status={DOMAIN_STATUS[domain.status]} />
          {domain.status !== 'removing' && (
            <Button
              size="sm"
              icon={<RefreshCw size={14} aria-hidden="true" />}
              loading={check.isPending}
              loadingLabel="Checking…"
              onClick={async () => {
                if ((await check.run()).ok) onChange();
              }}
            >
              Check now
            </Button>
          )}
          {canEdit && domain.status !== 'removing' && (
            <Button
              size="sm"
              variant="danger"
              icon={<Trash2 size={14} aria-hidden="true" />}
              loading={remove.isPending}
              loadingLabel="Removing…"
              onClick={async () => {
                if ((await remove.run()).ok) onChange();
              }}
            >
              Remove
            </Button>
          )}
        </div>
      </div>
      {domain.message && <p className="text-muted text-small">{domain.message}</p>}
      {(check.error || remove.error) && (
        <Alert tone="danger" title="That did not work">
          {(check.error ?? remove.error)?.message}
        </Alert>
      )}
      {domain.records.length > 0 && domain.status !== 'active' && (
        <TableScroll label={`DNS records for ${domain.domain}`}>
          <table className="data-table data-table--stack">
            <caption className="visually-hidden">DNS records to create for {domain.domain}</caption>
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Name</th>
                <th scope="col">Value</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              {domain.records.map((r) => (
                <tr key={`${r.type}-${r.name}-${r.value}`}>
                  <td data-label="Type" className="mono">
                    {r.type}
                  </td>
                  <td data-label="Name">
                    <span className="value-row">
                      <code>{r.name}</code>
                      <CopyButton value={r.name} label={`${r.type} record name`} />
                    </span>
                  </td>
                  <td data-label="Value">
                    <span className="value-row">
                      <code>{r.value}</code>
                      <CopyButton value={r.value} label={`${r.type} record value`} />
                    </span>
                  </td>
                  <td data-label="Why">{r.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </li>
  );
}

export function ApplicationDomainsTab() {
  const api = useApi();
  const demo = useDemoMode();
  const { can } = useAuth();
  const { notify } = useToast();
  const { application } = useApplication();
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);
  const caps = useResource(`capabilities:${application.provider}`, (signal) =>
    api.providers.getCapabilities(application.provider, signal),
  );
  const domains = useResource(
    `application:${application.id}:domains`,
    (signal) => api.platform.listDomains(application.id, signal),
    { pollWhile: (list) => list.some((d) => d.status === 'verifying' || d.status === 'removing'), pollIntervalMs: 10_000 },
  );
  const add = useMutation((domain: string) => api.platform.addDomain(application.id, domain));

  if (caps.data && !caps.data.features.custom_domains) {
    return (
      <Panel title="Custom domains">
        <EmptyState
          icon={Globe}
          title="Not available for this provider"
          message={`${providerLabel(application.provider)} does not support custom domains through CloudBridge yet.`}
          compact
        />
      </Panel>
    );
  }

  const value = name.trim().toLowerCase().replace(/\.$/, '');
  const problem = value && !DOMAIN.test(value) ? 'Enter a domain you own, such as api.example.com.' : null;
  const canEdit = can('applications:update');

  const onAdd = async (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!value || problem) return;
    const result = await add.run(value);
    if (result.ok) {
      setName('');
      setTouched(false);
      domains.setData((list) => [...(list ?? []), result.data]);
      notify(simulated({ tone: 'info', title: `${result.data.domain} added`, message: 'Create the DNS records shown to finish setup.' }, demo));
    }
  };

  return (
    <div className="stack">
      {canEdit && (
        <Panel title="Add a domain">
          {!application.current_version ? (
            <p className="text-muted">Deploy the application before adding a domain.</p>
          ) : (
            <form className="form" onSubmit={onAdd} noValidate aria-label="Add a custom domain">
              <FormErrorSummary error={add.error} listServerFieldErrors />
              <div className="form-grid">
                <TextField
                  label="Domain"
                  mono
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="api.example.com"
                  hint="A domain whose DNS you can edit. CloudBridge shows the records to create; the provider issues the HTTPS certificate."
                  value={name}
                  error={(touched ? problem : null) ?? fieldErrorFor(add.error, 'domain')}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={() => setTouched(true)}
                />
              </div>
              <div className="form-actions">
                <Button type="submit" variant="primary" icon={<Plus size={16} aria-hidden="true" />} loading={add.isPending} loadingLabel="Adding…">
                  Add domain
                </Button>
              </div>
            </form>
          )}
        </Panel>
      )}
      <Panel title="Custom domains">
        {domains.status === 'loading' ? (
          <CardSkeleton lines={3} label="Loading domains" />
        ) : domains.error ? (
          <ErrorState error={domains.error} onRetry={domains.reload} compact />
        ) : !domains.data?.length ? (
          <EmptyState icon={Globe} title="No custom domains" message="The application is reachable at its provider address." compact />
        ) : (
          <ul className="domain-list">
            {domains.data.map((d) => (
              <DomainItem key={d.id} domain={d} appId={application.id} canEdit={canEdit} onChange={domains.reload} />
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
