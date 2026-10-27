import { Save } from 'lucide-react';
import { useMemo, useRef, type FormEvent } from 'react';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { Button } from '../../components/forms/Button';
import { SelectField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { useFormState } from '../../components/forms/useFormState';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import type { TimeFormat, UpdateProfileRequest, User } from '../../types/api';
import { formatDateTime, humanize } from '../../utils/format';
import { messages, simulated } from '../../utils/messages';
import { compose, maxLength, required } from '../../utils/validation';
import { SettingsNav } from './SettingsNav';

function timeZones(): string[] {
  try {
    const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    return intl.supportedValuesOf?.('timeZone') ?? ['UTC'];
  } catch {
    return ['UTC'];
  }
}

function ProfileForm({ user }: { user: User }) {
  const api = useApi();
  const demo = useDemoMode();
  const { updateUser } = useAuth();
  const { notify } = useToast();
  const summaryRef = useRef<HTMLDivElement>(null);
  const zones = useMemo(() => timeZones(), []);
  const validators = useMemo(() => ({ display_name: compose(required('Display name'), maxLength('Display name', 80)) }), []);
  const form = useFormState(
    { display_name: user.display_name, timezone: user.preferences.timezone ?? '', time_format: user.preferences.time_format },
    validators,
  );
  const { blocker } = useUnsavedChanges(form.isDirty);
  const save = useMutation((body: UpdateProfileRequest) => api.settings.updateProfile(body));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    form.setSubmitted(true);
    if (!form.isValid) {
      summaryRef.current?.focus();
      return;
    }
    const result = await save.run({
      display_name: form.values.display_name.trim(),
      preferences: { timezone: form.values.timezone || null, time_format: form.values.time_format as TimeFormat },
    });
    if (result.ok) {
      updateUser(result.data);
      form.reset({
        display_name: result.data.display_name,
        timezone: result.data.preferences.timezone ?? '',
        time_format: result.data.preferences.time_format,
      });
      notify(simulated(messages.profileSaved(), demo));
    } else if (result.error) {
      form.applyServerErrors(result.error, { 'preferences.timezone': 'timezone', 'preferences.time_format': 'time_format' });
      requestAnimationFrame(() => summaryRef.current?.focus());
    }
  };

  const preview = formatDateTime(new Date().toISOString(), {
    timezone: form.values.timezone || null,
    time_format: form.values.time_format as TimeFormat,
  });

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="Profile and preferences">
      <FormErrorSummary ref={summaryRef} error={save.error} fieldMessages={!save.error && form.submitted ? Object.values(form.clientErrors).filter((m): m is string => Boolean(m)) : []} />
      <Panel title="Profile">
        <div className="form-grid">
          <TextField
            label="Display name"
            required
            autoComplete="name"
            value={form.values.display_name}
            onChange={(e) => form.setValue('display_name', e.target.value)}
            onBlur={() => form.touch('display_name')}
            error={form.errorFor('display_name')}
            disabled={save.isPending}
          />
          <TextField label="Email" type="email" value={user.email} readOnly hint="Your sign-in email is managed by your organization." />
          <TextField label="Role" value={humanize(user.role)} readOnly hint="Roles are assigned by an organization administrator." />
        </div>
      </Panel>
      <Panel title="Preferences">
        <div className="form-grid">
          <SelectField
            label="Time zone"
            value={form.values.timezone}
            onChange={(e) => form.setValue('timezone', e.target.value)}
            error={form.errorFor('timezone')}
            disabled={save.isPending}
            hint="Used for all dates and times in CloudBridge."
          >
            <option value="">Browser default</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Time format"
            value={form.values.time_format}
            onChange={(e) => form.setValue('time_format', e.target.value)}
            error={form.errorFor('time_format')}
            disabled={save.isPending}
            hint={`Preview: ${preview}`}
          >
            <option value="24h">24-hour</option>
            <option value="12h">12-hour</option>
          </SelectField>
        </div>
      </Panel>
      <div className="form-actions">
        <Button onClick={() => form.reset({ display_name: user.display_name, timezone: user.preferences.timezone ?? '', time_format: user.preferences.time_format })} disabled={!form.isDirty || save.isPending}>
          Discard changes
        </Button>
        <Button type="submit" variant="primary" loading={save.isPending} loadingLabel="Saving…" disabled={!form.isDirty || (form.submitted && !form.isValid)} icon={<Save size={16} aria-hidden="true" />}>
          Save changes
        </Button>
      </div>
      <UnsavedChangesDialog blocker={blocker} />
    </form>
  );
}

export function ProfilePage() {
  const { session } = useAuth();
  return (
    <div className="page">
      <PageHeader title="Settings" documentTitle="Profile and preferences" breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Profile' }]} />
      <SettingsNav />
      {session && <ProfileForm user={session.user} />}
    </div>
  );
}
