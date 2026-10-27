import { GitBranch, KeyRound, Moon, Pause, Play, Rocket, Save, Trash2, Upload } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { CopyButton } from '../../components/forms/CopyButton';
import { Checkbox, SelectField, TextAreaField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { Panel } from '../../components/layout/Panel';
import { StatusBadge } from '../../components/status/StatusBadge';
import { applicationStatus } from '../../components/status/statusMaps';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import type { AppEnvironment, Automation, AutomationInput, SleepSchedule, WebhookSecret } from '../../types/api';
import { simulated } from '../../utils/messages';
import { gitBranch } from '../../utils/validation';
import { useApplication } from './applicationContext';
import { fieldErrorFor } from '../../services/api/errors';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const ENVIRONMENT_TEXT: Record<AppEnvironment, string> = {
  production: 'Production',
  staging: 'Staging',
  preview: 'Pull request preview',
};

function toInput(a: Automation): AutomationInput {
  return {
    environment: a.environment,
    promote_to_id: a.promote_to_id,
    auto_deploy: a.auto_deploy,
    auto_deploy_branch: a.auto_deploy_branch,
    previews_enabled: a.previews_enabled,
    config_from_repo: a.config_from_repo,
    requires_approval: a.requires_approval,
  };
}

function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Deploy on push, pull-request previews, environments and promotion, approvals, cloudbridge.yaml and sleep schedules. */
export function ApplicationAutomationTab() {
  const api = useApi();
  const demo = useDemoMode();
  const { can } = useAuth();
  const { notify } = useToast();
  const navigate = useNavigate();
  const idempotencyKey = useIdempotencyKey();
  const { application, resource } = useApplication();
  const id = application.id;
  const repository = application.source.type === 'repository';
  const canUpdate = can('applications:update');

  const automation = useResource(`application:${id}:automation`, (signal) => api.platform.getAutomation(id, signal));
  const schedule = useResource(`application:${id}:schedule`, (signal) => api.platform.getSchedule(id, signal));
  const caps = useResource(`capabilities:${application.provider}`, (signal) =>
    api.providers.getCapabilities(application.provider, signal),
  );
  const apps = useResource('automation:apps', (signal) => api.applications.list({ page: 1, page_size: 100 }, signal));
  const previews = useResource(`application:${id}:previews`, (signal) => api.platform.listPreviews(id, signal), {
    enabled: repository,
  });

  const [draft, setDraft] = useState<AutomationInput | null>(null);
  const [secret, setSecret] = useState<WebhookSecret | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<SleepSchedule>({
    timezone: localTimeZone(),
    days: [0, 1, 2, 3, 4],
    sleep_at: '20:00',
    wake_at: '08:00',
  });
  const [configText, setConfigText] = useState('');

  const loaded = automation.data;
  // Seed the forms from what the backend returned (state adjusted during render, not in an effect).
  const [seeded, setSeeded] = useState<Automation | undefined>(undefined);
  if (loaded && loaded !== seeded) {
    setSeeded(loaded);
    setDraft(toInput(loaded));
  }
  const savedSchedule = schedule.data?.schedule ?? null;
  const [seededSchedule, setSeededSchedule] = useState<SleepSchedule | null>(null);
  if (savedSchedule && savedSchedule !== seededSchedule) {
    setSeededSchedule(savedSchedule);
    setScheduleDraft(savedSchedule);
  }

  const save = useMutation((input: AutomationInput) => api.platform.updateAutomation(id, input));
  const rotate = useMutation(() => api.platform.rotateWebhookSecret(id));
  const promote = useMutation((key: string) => api.platform.promote(id, { idempotencyKey: key }));
  const saveSchedule = useMutation((input: SleepSchedule) => api.platform.setSchedule(id, input));
  const removeSchedule = useMutation(() => api.platform.deleteSchedule(id));
  const togglePause = useMutation((pause: boolean) => (pause ? api.platform.pause(id) : api.platform.resume(id)));
  const applyFile = useMutation(async (content: string) => {
    const config = await api.applications.getConfiguration(id);
    return api.platform.applyConfigFile(id, { content, version: config.version });
  });

  if (automation.status === 'loading' || !draft) return <CardSkeleton lines={6} label="Loading automation settings" />;
  if (automation.error || !loaded) return <ErrorState error={automation.error} onRetry={automation.reload} />;

  const branchError = draft.auto_deploy_branch && gitBranch(draft.auto_deploy_branch);
  const targets = (apps.data?.items ?? []).filter((a) => a.id !== id && a.provider === application.provider);
  const dirty = JSON.stringify(draft) !== JSON.stringify(toInput(loaded));
  const pauseSupported = caps.data?.features.pause ?? false;

  const onSave = async (event: FormEvent) => {
    event.preventDefault();
    if (branchError) return;
    const result = await save.run({ ...draft, auto_deploy_branch: draft.auto_deploy_branch?.trim() || null });
    if (result.ok) {
      automation.setData(() => result.data);
      setDraft(toInput(result.data));
      resource.reload();
      notify(simulated({ tone: 'success', title: 'Automation settings saved' }, demo));
    }
  };

  const onPromote = async () => {
    const result = await promote.run(idempotencyKey({ promote: id, version: application.current_version }));
    if (result.ok) {
      notify(simulated({ tone: 'info', title: 'Promotion started', message: `Deploying ${application.current_version} to the next environment.` }, demo));
      navigate(`/deployments/${encodeURIComponent(result.data.id)}`);
    }
  };

  const onPause = async (pause: boolean) => {
    const result = await togglePause.run(pause);
    if (result.ok) {
      schedule.setData(() => result.data);
      resource.reload();
      notify(simulated({ tone: 'success', title: pause ? 'Application paused' : 'Application resumed' }, demo));
    }
  };

  const toggleDay = (day: number) =>
    setScheduleDraft((s) => ({
      ...s,
      days: s.days.includes(day) ? s.days.filter((d) => d !== day) : [...s.days, day].sort((a, b) => a - b),
    }));

  return (
    <div className="stack">
      <form className="form" onSubmit={onSave} noValidate aria-label="Automation settings">
        <FormErrorSummary error={save.error} fieldMessages={branchError ? [branchError] : []} listServerFieldErrors />
        <Panel title="Deploy from GitHub">
          {!repository ? (
            <p className="text-muted">
              This application deploys a container image. Auto-deploy, pull-request previews and cloudbridge.yaml need a
              repository source.
            </p>
          ) : (
            <div className="stack">
              <Checkbox
                label="Deploy every push"
                description="A push to the branch below builds and deploys that exact commit."
                checked={draft.auto_deploy}
                disabled={!canUpdate || save.isPending}
                onChange={(e) => setDraft({ ...draft, auto_deploy: e.target.checked })}
              />
              <TextField
                label="Branch"
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder={application.source.ref_type === 'branch' ? (application.source.git_ref ?? 'main') : 'main'}
                hint="Leave empty to use the application's branch."
                value={draft.auto_deploy_branch ?? ''}
                error={branchError || fieldErrorFor(save.error, 'auto_deploy_branch')}
                disabled={!canUpdate || save.isPending || !draft.auto_deploy}
                onChange={(e) => setDraft({ ...draft, auto_deploy_branch: e.target.value })}
              />
              <Checkbox
                label="Preview every pull request"
                description="Each pull request gets its own temporary application and address, removed when it closes. Pull requests from forks are ignored."
                checked={draft.previews_enabled}
                disabled={!canUpdate || save.isPending}
                onChange={(e) => setDraft({ ...draft, previews_enabled: e.target.checked })}
              />
              <Checkbox
                label="Read settings from cloudbridge.yaml"
                description="Before each deployment, CloudBridge reads cloudbridge.yaml at the deployed commit and makes it the requested configuration."
                checked={draft.config_from_repo}
                disabled={!canUpdate || save.isPending}
                onChange={(e) => setDraft({ ...draft, config_from_repo: e.target.checked })}
              />
              <div>
                <span className="field__label">Webhook address</span>
                <span className="value-row">
                  <code>{loaded.webhook.url}</code>
                  <CopyButton value={loaded.webhook.url} label="webhook address" />
                </span>
                <p className="field__hint">
                  In GitHub: Settings → Webhooks → Add webhook. Content type <code>application/json</code>, events “Pushes” and
                  “Pull requests”, and the secret below.{' '}
                  {loaded.webhook.secret_configured ? 'A secret is set.' : 'No secret has been created yet.'}
                </p>
                {canUpdate && (
                  <Button
                    size="sm"
                    icon={<KeyRound size={14} aria-hidden="true" />}
                    loading={rotate.isPending}
                    loadingLabel="Creating…"
                    onClick={async () => {
                      const result = await rotate.run();
                      if (result.ok) {
                        setSecret(result.data);
                        automation.reload();
                      }
                    }}
                  >
                    {loaded.webhook.secret_configured ? 'Replace webhook secret' : 'Create webhook secret'}
                  </Button>
                )}
                {rotate.error && <Alert tone="danger" title="The secret could not be created">{rotate.error.message}</Alert>}
                {secret && (
                  <Alert tone="warning" title="Copy the secret now" role="status">
                    It is shown only once. Paste it into the GitHub webhook's “Secret” field.
                    <span className="value-row">
                      <code>{secret.secret}</code>
                      <CopyButton value={secret.secret} label="webhook secret" />
                    </span>
                  </Alert>
                )}
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Environment and promotion">
          <div className="form-grid">
            <SelectField
              label="Environment"
              value={draft.environment}
              disabled={!canUpdate || save.isPending || draft.environment === 'preview'}
              onChange={(e) => setDraft({ ...draft, environment: e.target.value === 'staging' ? 'staging' : 'production' })}
            >
              {draft.environment === 'preview' ? (
                <option value="preview">{ENVIRONMENT_TEXT.preview}</option>
              ) : (
                <>
                  <option value="production">{ENVIRONMENT_TEXT.production}</option>
                  <option value="staging">{ENVIRONMENT_TEXT.staging}</option>
                </>
              )}
            </SelectField>
            <SelectField
              label="Promotes to"
              value={draft.promote_to_id ?? ''}
              hint="Promotion deploys this application's exact image (same digest) with the target's own settings."
              disabled={!canUpdate || save.isPending || draft.environment === 'preview'}
              onChange={(e) => setDraft({ ...draft, promote_to_id: e.target.value || null })}
            >
              <option value="">No promotion target</option>
              {targets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </SelectField>
          </div>
          {loaded.promote_to_id && can('deployments:create') && (
            <div className="form-actions">
              <Button
                icon={<Rocket size={16} aria-hidden="true" />}
                loading={promote.isPending}
                loadingLabel="Starting…"
                disabled={!application.current_version}
                title={application.current_version ? undefined : 'Deploy this application first'}
                onClick={() => void onPromote()}
              >
                Promote {application.current_version ?? ''}
              </Button>
            </div>
          )}
          {promote.error && <Alert tone="danger" title="Promotion did not start">{promote.error.message}</Alert>}
        </Panel>

        <Panel title="Approvals">
          <Checkbox
            label="Require approval for production deployments"
            description="Deployments wait until an owner or admin approves them. Rollbacks never wait."
            checked={draft.requires_approval}
            disabled={!can('deployments:approve') || save.isPending || draft.environment !== 'production'}
            onChange={(e) => setDraft({ ...draft, requires_approval: e.target.checked })}
          />
          {!can('deployments:approve') && <p className="field__hint">Only owners and admins can change this.</p>}
        </Panel>

        {canUpdate && (
          <div className="form-actions">
            <Button type="submit" variant="primary" loading={save.isPending} loadingLabel="Saving…" disabled={!dirty} icon={<Save size={16} aria-hidden="true" />}>
              Save automation settings
            </Button>
          </div>
        )}
      </form>

      {repository && loaded.previews_enabled && (
        <Panel title="Pull request previews">
          {previews.data && previews.data.length > 0 ? (
            <ul className="stack">
              {previews.data.map((p) => (
                <li key={p.id} className="value-row">
                  <GitBranch size={14} aria-hidden="true" />
                  <Link to={`/applications/${encodeURIComponent(p.id)}`}>{p.name}</Link>
                  <StatusBadge status={applicationStatus[p.status]} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">No open pull request has a preview right now.</p>
          )}
        </Panel>
      )}

      <Panel title="Sleep schedule">
        {!pauseSupported ? (
          <p className="text-muted">This provider cannot pause services through CloudBridge yet.</p>
        ) : (
          <div className="stack">
            <div className="value-row">
              <Moon size={16} aria-hidden="true" />
              <span>
                {schedule.data?.paused
                  ? schedule.data.paused_by_schedule
                    ? 'Asleep: paused by its schedule.'
                    : 'Paused by a person; it stays paused until someone resumes it.'
                  : 'Serving traffic.'}
              </span>
              {can('deployments:create') && application.current_version && (
                <Button
                  size="sm"
                  icon={schedule.data?.paused ? <Play size={14} aria-hidden="true" /> : <Pause size={14} aria-hidden="true" />}
                  loading={togglePause.isPending}
                  loadingLabel="Working…"
                  onClick={() => void onPause(!schedule.data?.paused)}
                >
                  {schedule.data?.paused ? 'Resume now' : 'Pause now'}
                </Button>
              )}
            </div>
            {togglePause.error && <Alert tone="danger" title="That did not work">{togglePause.error.message}</Alert>}
            <p className="text-muted text-small">
              Pause quiet environments overnight to save money. A paused application costs little or nothing and comes back
              with the same version.
            </p>
            <div className="form-grid">
              <TextField
                label="Time zone"
                mono
                value={scheduleDraft.timezone}
                disabled={!canUpdate}
                error={fieldErrorFor(saveSchedule.error, 'timezone')}
                onChange={(e) => setScheduleDraft({ ...scheduleDraft, timezone: e.target.value })}
              />
              <TextField
                label="Sleep at"
                type="time"
                value={scheduleDraft.sleep_at}
                disabled={!canUpdate}
                error={fieldErrorFor(saveSchedule.error, 'sleep_at')}
                onChange={(e) => setScheduleDraft({ ...scheduleDraft, sleep_at: e.target.value })}
              />
              <TextField
                label="Wake at"
                type="time"
                value={scheduleDraft.wake_at}
                disabled={!canUpdate}
                error={fieldErrorFor(saveSchedule.error, 'wake_at')}
                onChange={(e) => setScheduleDraft({ ...scheduleDraft, wake_at: e.target.value })}
              />
            </div>
            <fieldset className="fieldset" disabled={!canUpdate}>
              <legend>Starts on</legend>
              <div className="cluster">
                {DAYS.map((label, day) => (
                  <Checkbox key={label} label={label} checked={scheduleDraft.days.includes(day)} onChange={() => toggleDay(day)} />
                ))}
              </div>
            </fieldset>
            <FormErrorSummary error={saveSchedule.error ?? removeSchedule.error} listServerFieldErrors />
            {canUpdate && (
              <div className="form-actions">
                {schedule.data?.schedule && (
                  <Button
                    icon={<Trash2 size={16} aria-hidden="true" />}
                    loading={removeSchedule.isPending}
                    onClick={async () => {
                      const result = await removeSchedule.run();
                      if (result.ok) schedule.setData(() => result.data);
                    }}
                  >
                    Remove schedule
                  </Button>
                )}
                <Button
                  variant="primary"
                  icon={<Save size={16} aria-hidden="true" />}
                  loading={saveSchedule.isPending}
                  loadingLabel="Saving…"
                  onClick={async () => {
                    const result = await saveSchedule.run(scheduleDraft);
                    if (result.ok) {
                      schedule.setData(() => result.data);
                      notify(simulated({ tone: 'success', title: 'Sleep schedule saved' }, demo));
                    }
                  }}
                >
                  Save schedule
                </Button>
              </div>
            )}
          </div>
        )}
      </Panel>

      {canUpdate && (
        <Panel title="Apply a cloudbridge.yaml">
          <div className="stack">
            <TextAreaField
              label="cloudbridge.yaml"
              mono
              rows={8}
              spellCheck={false}
              placeholder={'port: 8080\ncpu: "0.5"\nmemory: 1Gi\ninstances: {min: 1, max: 3}\nenvironment:\n  LOG_LEVEL: info'}
              hint="Sets the requested configuration now; deploy afterwards to apply it."
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
            />
            <FormErrorSummary error={applyFile.error} listServerFieldErrors />
            <div className="form-actions">
              <Button
                icon={<Upload size={16} aria-hidden="true" />}
                loading={applyFile.isPending}
                loadingLabel="Applying…"
                disabled={!configText.trim()}
                onClick={async () => {
                  const result = await applyFile.run(configText);
                  if (result.ok) {
                    notify(simulated({ tone: 'success', title: 'Configuration updated from cloudbridge.yaml' }, demo));
                    setConfigText('');
                  }
                }}
              >
                Apply file
              </Button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
