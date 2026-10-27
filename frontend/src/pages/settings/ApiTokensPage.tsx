import { KeyRound, Plus, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { CopyButton } from '../../components/forms/CopyButton';
import { SelectField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { RelativeTime } from '../../components/status/RelativeTime';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { simulated } from '../../utils/messages';
import { SettingsNav } from './SettingsNav';
import { TableScroll } from '../../components/tables/TableScroll';
import { fieldErrorFor } from '../../services/api/errors';

const EXPIRY = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' },
  { value: '', label: 'Never' },
];

export function ApiTokensPage() {
  const api = useApi();
  const demo = useDemoMode();
  const { notify } = useToast();
  const tokens = useResource('settings:api-tokens', (signal) => api.platform.listApiTokens(signal));
  const [name, setName] = useState('');
  const [expiry, setExpiry] = useState('90');
  const [created, setCreated] = useState<string | null>(null);
  const create = useMutation(() =>
    api.platform.createApiToken({ name: name.trim(), expires_in_days: expiry ? Number(expiry) : null }),
  );
  const revoke = useMutation((id: string) => api.platform.revokeApiToken(id));

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run();
    if (result.ok) {
      setCreated(result.data.token);
      setName('');
      tokens.reload();
      notify(simulated({ tone: 'success', title: 'API token created' }, demo));
    }
  };

  return (
    <div className="page">
      <PageHeader title="Settings" documentTitle="API tokens" breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'API tokens' }]} />
      <SettingsNav />
      <p className="text-muted">
        Tokens let the CloudBridge CLI and CI pipelines (such as the GitHub Action) act as you, with your role. Send one as{' '}
        <code>Authorization: Bearer &lt;token&gt;</code>. Only a hash is stored; revoke a token as soon as it is no longer needed.
      </p>
      <Panel title="Create a token">
        <form className="form" onSubmit={onCreate} noValidate aria-label="Create an API token">
          <FormErrorSummary error={create.error} listServerFieldErrors />
          <div className="form-grid">
            <TextField
              label="Name"
              required
              autoComplete="off"
              placeholder="GitHub Actions"
              value={name}
              onChange={(e) => setName(e.target.value)}
              error={fieldErrorFor(create.error, 'name')}
            />
            <SelectField label="Expires after" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              {EXPIRY.map((o) => (
                <option key={o.label} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
          </div>
          <div className="form-actions">
            <Button type="submit" variant="primary" icon={<Plus size={16} aria-hidden="true" />} loading={create.isPending} loadingLabel="Creating…" disabled={!name.trim()}>
              Create token
            </Button>
          </div>
        </form>
        {created && (
          <Alert tone="warning" title="Copy the token now" role="status">
            It is shown only once. Store it as a secret, e.g. <code>CLOUDBRIDGE_TOKEN</code> in your CI.
            <span className="value-row">
              <code>{created}</code>
              <CopyButton value={created} label="API token" />
            </span>
          </Alert>
        )}
      </Panel>
      <Panel title="Your tokens">
        {tokens.status === 'loading' ? (
          <CardSkeleton lines={3} label="Loading tokens" />
        ) : tokens.error ? (
          <ErrorState error={tokens.error} onRetry={tokens.reload} compact />
        ) : !tokens.data?.length ? (
          <EmptyState icon={KeyRound} title="No API tokens" message="Create one to use the CLI or deploy from CI." compact />
        ) : (
          <TableScroll label="Your API tokens">
            <table className="data-table data-table--stack">
              <caption className="visually-hidden">Your API tokens</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Token</th>
                  <th scope="col">Last used</th>
                  <th scope="col">Expires</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {tokens.data.map((t) => (
                  <tr key={t.id}>
                    <th scope="row" data-label="Name">
                      {t.name}
                    </th>
                    <td data-label="Token" className="mono">
                      {t.prefix}…
                    </td>
                    <td data-label="Last used">{t.last_used_at ? <RelativeTime value={t.last_used_at} /> : 'Never'}</td>
                    <td data-label="Expires">{t.expires_at ? <RelativeTime value={t.expires_at} /> : 'Never'}</td>
                    <td data-label="Actions">
                      <Button
                        size="sm"
                        variant="danger"
                        icon={<Trash2 size={14} aria-hidden="true" />}
                        loading={revoke.isPending}
                        aria-label={`Revoke ${t.name}`}
                        onClick={async () => {
                          if ((await revoke.run(t.id)).ok) tokens.reload();
                        }}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
        {revoke.error && <Alert tone="danger" title="The token was not revoked">{revoke.error.message}</Alert>}
      </Panel>
    </div>
  );
}
