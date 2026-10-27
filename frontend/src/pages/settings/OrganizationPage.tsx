import { Save, Users } from 'lucide-react';
import { useMemo, useRef, type FormEvent } from 'react';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { TableSkeleton } from '../../components/feedback/TableSkeleton';
import { Button } from '../../components/forms/Button';
import { TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { useFormState } from '../../components/forms/useFormState';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { DateTime } from '../../components/status/RelativeTime';
import { DataTable } from '../../components/tables/DataTable';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import type { Organization, OrganizationMember } from '../../types/api';
import { humanize } from '../../utils/format';
import { messages, simulated } from '../../utils/messages';
import { compose, maxLength, required } from '../../utils/validation';
import { FeatureUnavailable } from './FeatureUnavailable';
import { SettingsNav } from './SettingsNav';

function OrganizationForm({ organization, onSaved }: { organization: Organization; onSaved(org: Organization): void }) {
  const api = useApi();
  const demo = useDemoMode();
  const { can, updateOrganizationName } = useAuth();
  const { notify } = useToast();
  const summaryRef = useRef<HTMLDivElement>(null);
  const readOnly = !can('organization:manage');
  const validators = useMemo(() => ({ name: compose(required('Organization name'), maxLength('Organization name', 100)) }), []);
  const form = useFormState({ name: organization.name }, validators);
  const { blocker } = useUnsavedChanges(form.isDirty);
  const save = useMutation((name: string) => api.settings.updateOrganization({ name }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    form.setSubmitted(true);
    if (!form.isValid) {
      summaryRef.current?.focus();
      return;
    }
    const result = await save.run(form.values.name.trim());
    if (result.ok) {
      form.reset({ name: result.data.name });
      updateOrganizationName(result.data.name);
      onSaved(result.data);
      notify(simulated(messages.organizationSaved(), demo));
    } else if (result.error) {
      form.applyServerErrors(result.error);
      requestAnimationFrame(() => summaryRef.current?.focus());
    }
  };

  return (
    <form onSubmit={submit} noValidate aria-label="Organization">
      <Panel
        title="Organization"
        footer={
          !readOnly && (
            <div className="form-actions">
              <Button type="submit" variant="primary" loading={save.isPending} loadingLabel="Saving…" disabled={!form.isDirty || (form.submitted && !form.isValid)} icon={<Save size={16} aria-hidden="true" />}>
                Save
              </Button>
            </div>
          )
        }
      >
        <div className="form">
          <FormErrorSummary ref={summaryRef} error={save.error} />
          <div className="form-grid">
            <TextField
              label="Organization name"
              required
              value={form.values.name}
              onChange={(e) => form.setValue('name', e.target.value)}
              onBlur={() => form.touch('name')}
              error={form.errorFor('name')}
              readOnly={readOnly}
              disabled={save.isPending}
              hint={readOnly ? 'Only administrators can rename the organization.' : undefined}
            />
            <div className="field">
              <span className="field__label">Created</span>
              <span>
                <DateTime value={organization.created_at} />
              </span>
            </div>
          </div>
        </div>
      </Panel>
      <UnsavedChangesDialog blocker={blocker} />
    </form>
  );
}

export function OrganizationPage() {
  const api = useApi();
  const { session } = useAuth();
  const supported = Boolean(session?.features.organization_settings);
  const org = useResource('settings:organization', (signal) => api.settings.getOrganization(signal), { enabled: supported });
  const members = useResource('settings:members', (signal) => api.settings.listMembers(signal), { enabled: supported });

  const header = (
    <>
      <PageHeader title="Settings" documentTitle="Organization settings" breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Organization' }]} />
      <SettingsNav />
    </>
  );

  if (!supported || org.error?.status === 404) {
    return (
      <div className="page">
        {header}
        <FeatureUnavailable feature="Organization settings" />
      </div>
    );
  }

  return (
    <div className="page">
      {header}
      {org.status === 'loading' && (
        <Panel>
          <CardSkeleton lines={2} label="Loading organization" />
        </Panel>
      )}
      {org.status === 'error' && (
        <Panel>
          <ErrorState error={org.error} onRetry={org.reload} />
        </Panel>
      )}
      {org.status === 'success' && org.data && <OrganizationForm key={org.data.name} organization={org.data} onSaved={(o) => org.setData(() => o)} />}

      <Panel title="Members" flush>
        {members.status === 'loading' && <TableSkeleton columns={4} rows={3} label="Loading members" />}
        {members.status === 'error' && <ErrorState compact error={members.error} onRetry={members.reload} />}
        {members.status === 'success' &&
          (members.data?.length ? (
            <DataTable<OrganizationMember>
              caption="Organization members"
              rows={members.data}
              rowKey={(m) => m.id}
              columns={[
                { key: 'name', header: 'Name', render: (m) => <span className="data-table__primary">{m.display_name}</span> },
                { key: 'email', header: 'Email', render: (m) => m.email },
                { key: 'role', header: 'Role', render: (m) => humanize(m.role) },
                { key: 'joined', header: 'Joined', render: (m) => <DateTime value={m.joined_at} /> },
              ]}
            />
          ) : (
            <EmptyState compact icon={Users} title="No members" message="No members were returned for this organization." />
          ))}
        <div className="panel__footer text-small text-muted">Inviting members and changing roles is not available in this version of CloudBridge.</div>
      </Panel>
    </div>
  );
}
