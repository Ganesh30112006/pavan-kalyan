import { BellRing, Plus, Send, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { CopyButton } from '../../components/forms/CopyButton';
import { Checkbox, SelectField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { RelativeTime } from '../../components/status/RelativeTime';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { NOTIFICATION_EVENTS } from '../../services/api/decoders';
import { fieldErrorFor } from '../../services/api/errors';
import type { AlertChannel, AlertChannelKind, NotificationEvent } from '../../types/api';
import { simulated } from '../../utils/messages';
import { EVENT_TEXT } from './eventText';
import { SettingsNav } from './SettingsNav';

const KIND_TEXT: Record<AlertChannelKind, { label: string; hint: string; placeholder: string }> = {
  slack: {
    label: 'Slack',
    hint: 'A Slack incoming webhook (Apps → Incoming Webhooks).',
    placeholder: 'https://hooks.slack.com/services/…',
  },
  teams: {
    label: 'Microsoft Teams',
    hint: 'A Teams Workflows webhook or incoming webhook address.',
    placeholder: 'https://….webhook.office.com/…',
  },
  webhook: {
    label: 'Signed webhook',
    hint: 'Any HTTPS endpoint. Each request carries an X-CloudBridge-Signature (HMAC-SHA256) header.',
    placeholder: 'https://hooks.example.com/cloudbridge',
  },
};

const DEFAULT_EVENTS: NotificationEvent[] = ['deployment_failed', 'rollback_completed', 'application_unhealthy', 'approval_requested'];

function ChannelItem({ channel, canManage, onChange }: { channel: AlertChannel; canManage: boolean; onChange(): void }) {
  const api = useApi();
  const test = useMutation(() => api.platform.testAlertChannel(channel.id));
  const remove = useMutation(() => api.platform.deleteAlertChannel(channel.id));
  const [result, setResult] = useState<{ delivered: boolean; status: string } | null>(null);
  return (
    <li className="domain-item">
      <div className="domain-item__header">
        <div>
          <h3 className="domain-item__name">{channel.name}</h3>
          <p className="text-muted text-small">
            {KIND_TEXT[channel.kind].label} · {channel.host}
            {channel.signed ? ' · signed' : ''}
            {!channel.enabled ? ' · off' : ''}
          </p>
        </div>
        {canManage && (
          <div className="cluster">
            <Button
              size="sm"
              icon={<Send size={14} aria-hidden="true" />}
              loading={test.isPending}
              loadingLabel="Sending…"
              onClick={async () => {
                const outcome = await test.run();
                if (outcome.ok) {
                  setResult(outcome.data);
                  onChange();
                }
              }}
            >
              Send test
            </Button>
            <Button
              size="sm"
              variant="danger"
              icon={<Trash2 size={14} aria-hidden="true" />}
              loading={remove.isPending}
              onClick={async () => {
                if ((await remove.run()).ok) onChange();
              }}
            >
              Remove
            </Button>
          </div>
        )}
      </div>
      <p className="text-small">{channel.events.map((e) => EVENT_TEXT[e].label).join(' · ')}</p>
      {channel.last_delivery_at && (
        <p className="text-muted text-small">
          Last delivery <RelativeTime value={channel.last_delivery_at} />: {channel.last_delivery_status}
        </p>
      )}
      {result && (
        <Alert tone={result.delivered ? 'success' : 'warning'} title={result.delivered ? 'Test alert delivered' : 'Test alert not delivered'} role="status">
          {result.status}
        </Alert>
      )}
      {(test.error || remove.error) && <Alert tone="danger" title="That did not work">{(test.error ?? remove.error)?.message}</Alert>}
    </li>
  );
}

export function AlertsPage() {
  const api = useApi();
  const demo = useDemoMode();
  const { can } = useAuth();
  const { notify } = useToast();
  const canManage = can('providers:manage');
  const channels = useResource('settings:alert-channels', (signal) => api.platform.listAlertChannels(signal));
  const [kind, setKind] = useState<AlertChannelKind>('slack');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<NotificationEvent[]>(DEFAULT_EVENTS);
  const [secret, setSecret] = useState<string | null>(null);
  const create = useMutation(() => api.platform.createAlertChannel({ name: name.trim(), kind, url: url.trim(), events, enabled: true }));

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    const result = await create.run();
    if (result.ok) {
      setSecret(result.data.signing_secret);
      setName('');
      setUrl('');
      channels.reload();
      notify(simulated({ tone: 'success', title: `Alert channel ${result.data.name} added` }, demo));
    }
  };

  return (
    <div className="page">
      <PageHeader title="Settings" documentTitle="Alerts" breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Alerts' }]} />
      <SettingsNav />
      <p className="text-muted">
        Send failed deployments, rollbacks, unhealthy applications and approval requests to Slack, Microsoft Teams or your own
        endpoint. Webhook addresses work like passwords: CloudBridge stores them but only ever shows their host.
      </p>
      {canManage && (
        <Panel title="Add an alert channel">
          <form className="form" onSubmit={onCreate} noValidate aria-label="Add an alert channel">
            <FormErrorSummary error={create.error} listServerFieldErrors />
            <div className="form-grid">
              <SelectField label="Type" value={kind} onChange={(e) => setKind(e.target.value as AlertChannelKind)}>
                {(Object.keys(KIND_TEXT) as AlertChannelKind[]).map((k) => (
                  <option key={k} value={k}>
                    {KIND_TEXT[k].label}
                  </option>
                ))}
              </SelectField>
              <TextField
                label="Name"
                required
                autoComplete="off"
                placeholder="Ops channel"
                value={name}
                onChange={(e) => setName(e.target.value)}
                error={fieldErrorFor(create.error, 'name')}
              />
              <TextField
                label="Webhook address"
                required
                mono
                type="url"
                autoComplete="off"
                spellCheck={false}
                className="form-grid__full"
                placeholder={KIND_TEXT[kind].placeholder}
                hint={KIND_TEXT[kind].hint}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                error={fieldErrorFor(create.error, 'url')}
              />
            </div>
            <fieldset className="fieldset">
              <legend>Send alerts when</legend>
              <div className="stack">
                {NOTIFICATION_EVENTS.map((e) => (
                  <Checkbox
                    key={e}
                    label={EVENT_TEXT[e].label}
                    description={EVENT_TEXT[e].description}
                    checked={events.includes(e)}
                    onChange={(ev) => setEvents((list) => (ev.target.checked ? [...list, e] : list.filter((x) => x !== e)))}
                  />
                ))}
              </div>
            </fieldset>
            <div className="form-actions">
              <Button
                type="submit"
                variant="primary"
                icon={<Plus size={16} aria-hidden="true" />}
                loading={create.isPending}
                loadingLabel="Adding…"
                disabled={!name.trim() || !url.trim() || events.length === 0}
              >
                Add channel
              </Button>
            </div>
          </form>
          {secret && (
            <Alert tone="warning" title="Copy the signing secret now" role="status">
              It is shown only once. Use it to verify the X-CloudBridge-Signature header on each request.
              <span className="value-row">
                <code>{secret}</code>
                <CopyButton value={secret} label="signing secret" />
              </span>
            </Alert>
          )}
        </Panel>
      )}
      <Panel title="Alert channels">
        {channels.status === 'loading' ? (
          <CardSkeleton lines={3} label="Loading alert channels" />
        ) : channels.error ? (
          <ErrorState error={channels.error} onRetry={channels.reload} compact />
        ) : !channels.data?.length ? (
          <EmptyState icon={BellRing} title="No alert channels" message="Alerts are sent only by e-mail, to people who opted in." compact />
        ) : (
          <ul className="domain-list">
            {channels.data.map((c) => (
              <ChannelItem key={c.id} channel={c} canManage={canManage} onChange={channels.reload} />
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
