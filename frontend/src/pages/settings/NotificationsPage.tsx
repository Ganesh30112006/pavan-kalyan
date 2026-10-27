import { Save } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { Checkbox } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { NOTIFICATION_EVENTS } from '../../services/api/decoders';
import type { NotificationPreferences } from '../../types/api';
import { messages, simulated } from '../../utils/messages';
import { FeatureUnavailable } from './FeatureUnavailable';
import { EVENT_TEXT } from './eventText';
import { SettingsNav } from './SettingsNav';

function NotificationsForm({ initial, onSaved }: { initial: NotificationPreferences; onSaved(p: NotificationPreferences): void }) {
  const api = useApi();
  const demo = useDemoMode();
  const { notify } = useToast();
  const [baseline, setBaseline] = useState(initial);
  const [prefs, setPrefs] = useState(initial);
  const dirty = JSON.stringify(prefs) !== JSON.stringify(baseline);
  const { blocker } = useUnsavedChanges(dirty);
  const save = useMutation((body: NotificationPreferences) => api.settings.updateNotificationPreferences(body));

  const doSave = async () => {
    const result = await save.run(prefs);
    if (result.ok) {
      setBaseline(result.data);
      setPrefs(result.data);
      onSaved(result.data);
      notify(simulated(messages.notificationsSaved(), demo));
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void doSave();
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="Notification preferences">
      <FormErrorSummary error={save.error} onRetry={() => void doSave()} />
      <Panel title="Email notifications">
        <div className="stack">
          <Checkbox
            label="Send email notifications"
            description="Emails go to the address you sign in with."
            checked={prefs.email_enabled}
            onChange={(e) => setPrefs((p) => ({ ...p, email_enabled: e.target.checked }))}
            disabled={save.isPending}
          />
          <fieldset className="fieldset" disabled={!prefs.email_enabled || save.isPending}>
            <legend>Notify me when</legend>
            <div className="stack">
              {NOTIFICATION_EVENTS.map((event) => (
                <Checkbox
                  key={event}
                  label={EVENT_TEXT[event].label}
                  description={EVENT_TEXT[event].description}
                  checked={prefs.events[event]}
                  onChange={(e) => setPrefs((p) => ({ ...p, events: { ...p.events, [event]: e.target.checked } }))}
                />
              ))}
            </div>
          </fieldset>
        </div>
      </Panel>
      <div className="form-actions">
        <Button onClick={() => setPrefs(baseline)} disabled={!dirty || save.isPending}>
          Discard changes
        </Button>
        <Button type="submit" variant="primary" loading={save.isPending} loadingLabel="Saving…" disabled={!dirty} icon={<Save size={16} aria-hidden="true" />}>
          Save preferences
        </Button>
      </div>
      <UnsavedChangesDialog blocker={blocker} />
    </form>
  );
}

export function NotificationsPage() {
  const api = useApi();
  const { session } = useAuth();
  const supported = Boolean(session?.features.notification_preferences);
  const prefs = useResource('settings:notifications', (signal) => api.settings.getNotificationPreferences(signal), { enabled: supported });

  return (
    <div className="page">
      <PageHeader title="Settings" documentTitle="Notification preferences" breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Notifications' }]} />
      <SettingsNav />
      {!supported || prefs.error?.status === 404 ? (
        <FeatureUnavailable feature="Notification preferences" />
      ) : (
        <>
          {prefs.status === 'loading' && (
            <Panel>
              <CardSkeleton lines={4} label="Loading notification preferences" />
            </Panel>
          )}
          {prefs.status === 'error' && (
            <Panel>
              <ErrorState error={prefs.error} onRetry={prefs.reload} />
            </Panel>
          )}
          {prefs.status === 'success' && prefs.data && <NotificationsForm initial={prefs.data} onSaved={(p) => prefs.setData(() => p)} />}
        </>
      )}
    </div>
  );
}
