import { AlertTriangle, CheckCircle2, Save, ShieldCheck, Undo2 } from 'lucide-react';
import { useMemo, useRef, useState, type FormEvent } from 'react';
import { ConfirmDialog } from '../../components/dialogs/ConfirmDialog';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { Alert } from '../../components/feedback/Alert';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { EnvironmentVariablesEditor, SecretReferencesEditor } from '../../components/forms/EnvironmentEditor';
import { SelectField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { Panel } from '../../components/layout/Panel';
import { DateTime } from '../../components/status/RelativeTime';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useMutation } from '../../hooks/useMutation';
import { useResource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import type {
  ApplicationConfiguration,
  EffectiveConfiguration,
  Exposure,
  SpecValidationResult,
  UpdateConfigurationRequest,
} from '../../types/api';
import { fromEnvRows, fromSecretRows, toEnvRows, toSecretRows, validateRows, type EnvRow, type SecretRow } from '../../utils/environment';
import { exposureLabel } from '../../utils/labels';
import { messages, simulated } from '../../utils/messages';
import { CPU_OPTIONS, MEMORY_OPTIONS, providerLabel } from '../../utils/providers';
import { healthCheckPath, integerInRange, optionalIntegerInRange } from '../../utils/validation';
import { AccessDeniedPage } from '../errors/AccessDeniedPage';
import { useApplication } from './applicationContext';
import { TableScroll } from '../../components/tables/TableScroll';

interface Draft {
  port: string;
  cpu: string;
  memory: string;
  min_instances: string;
  max_instances: string;
  concurrency: string;
  exposure: string;
  health_check_path: string;
  environment: EnvRow[];
  secret_refs: SecretRow[];
}

function toDraft(config: ApplicationConfiguration): Draft {
  return {
    port: String(config.port),
    cpu: config.cpu,
    memory: config.memory,
    min_instances: String(config.min_instances),
    max_instances: String(config.max_instances),
    concurrency: config.concurrency === null ? '' : String(config.concurrency),
    exposure: config.exposure,
    health_check_path: config.health_check_path ?? '',
    environment: toEnvRows(config.environment),
    secret_refs: toSecretRows(config.secret_refs),
  };
}

function comparable(d: Draft) {
  return JSON.stringify({ ...d, environment: fromEnvRows(d.environment), secret_refs: fromSecretRows(d.secret_refs) });
}

type Errors = Partial<Record<string, string>>;

function validate(d: Draft): Errors {
  const errors: Errors = {};
  const set = (key: string, message: string | null) => {
    if (message) errors[key] = message;
  };
  set('port', integerInRange('Port', 1, 65535)(d.port));
  set('min_instances', integerInRange('Minimum instances', 0, 1000)(d.min_instances));
  set('max_instances', integerInRange('Maximum instances', 1, 1000)(d.max_instances));
  set('concurrency', optionalIntegerInRange('Request concurrency', 1, 1000)(d.concurrency));
  if (!errors.min_instances && !errors.max_instances && Number(d.min_instances) > Number(d.max_instances)) {
    errors.max_instances = 'Maximum instances must be at least the minimum.';
  }
  set('health_check_path', healthCheckPath(d.health_check_path));
  return { ...errors, ...validateRows(d.environment, d.secret_refs) };
}

const DIFF_ROWS: Array<{ key: Exclude<keyof EffectiveConfiguration, 'observed_at'>; label: string }> = [
  { key: 'port', label: 'Port' },
  { key: 'cpu', label: 'CPU (vCPU)' },
  { key: 'memory', label: 'Memory' },
  { key: 'min_instances', label: 'Minimum instances' },
  { key: 'max_instances', label: 'Maximum instances' },
  { key: 'concurrency', label: 'Request concurrency' },
  { key: 'exposure', label: 'Exposure' },
  { key: 'health_check_path', label: 'Health-check path' },
];

function show(value: string | number | null | undefined, key: string): string {
  if (value === null || value === undefined || value === '') return key === 'health_check_path' ? 'TCP check' : 'Provider default';
  if (key === 'exposure') return exposureLabel[value as Exposure] ?? String(value);
  return String(value);
}

/** Requested settings next to what the provider actually runs, with every difference called out in text. */
function RequestedVsEffective({ config }: { config: ApplicationConfiguration }) {
  const { application } = useApplication();
  const effective = config.effective;
  return (
    <Panel title="Requested vs effective configuration" flush>
      {!effective ? (
        <p className="panel__body text-muted">
          The effective configuration is reported by {providerLabel(application.provider)} after the first successful
          deployment.
        </p>
      ) : (
        <>
          <TableScroll label="Requested and effective configuration">
            <table className="data-table data-table--stack">
              <caption className="visually-hidden">Requested configuration compared with the effective provider configuration</caption>
              <thead>
                <tr>
                  <th scope="col">Setting</th>
                  <th scope="col">Requested</th>
                  <th scope="col">Effective on {providerLabel(application.provider)}</th>
                </tr>
              </thead>
              <tbody>
                {DIFF_ROWS.map(({ key, label }) => {
                  const requested = show(config[key as keyof ApplicationConfiguration] as string | number | null, key);
                  const actual = show(effective[key] as string | number | null, key);
                  const changed = requested !== actual;
                  return (
                    <tr key={key} className={changed ? 'config-diff__changed' : undefined}>
                      <th scope="row" data-label="Setting">
                        {label}
                      </th>
                      <td data-label="Requested" className="mono">
                        {requested}
                      </td>
                      <td data-label="Effective" className="mono">
                        {actual}
                        {changed && (
                          <span className="config-diff__marker">
                            {' '}
                            <AlertTriangle size={13} aria-hidden="true" /> differs
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
          {config.adjustments.length > 0 && (
            <div className="panel__body stack">
              <h3 className="text-small">Why the provider changed settings</h3>
              <ul className="plain-list stack">
                {config.adjustments.map((a) => (
                  <li key={a.field}>
                    <strong className="mono">{a.field}</strong>: {a.requested} → {a.effective}. <span className="text-muted">{a.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="panel__footer text-small text-muted">
            Observed <DateTime value={effective.observed_at} />. Saved changes take effect on the next deployment.
          </p>
        </>
      )}
    </Panel>
  );
}

function ValidationPanel({ dirty }: { dirty: boolean }) {
  const api = useApi();
  const { application } = useApplication();
  const check = useMutation(() => api.applications.validate(application.id));
  const [result, setResult] = useState<SpecValidationResult | null>(null);

  const run = async () => {
    setResult(null);
    const outcome = await check.run();
    if (outcome.ok) setResult(outcome.data);
  };

  return (
    <Panel
      title={`Validate against ${providerLabel(application.provider)}`}
      actions={
        <Button size="sm" icon={<ShieldCheck size={14} aria-hidden="true" />} onClick={run} loading={check.isPending} loadingLabel="Validating…" disabled={dirty}>
          Validate saved configuration
        </Button>
      }
    >
      <div className="stack">
        <p className="text-muted text-small">
          {dirty
            ? 'Save or discard your changes first: validation checks the saved configuration.'
            : 'Asks CloudBridge to check the saved configuration against the provider without deploying anything.'}
        </p>
        {check.error && <ErrorState compact error={check.error} onRetry={run} title="Validation could not run" />}
        {result && (
          <div className="stack" role="status">
            {result.valid ? (
              <p className="inline-status inline-status--success">
                <CheckCircle2 size={16} aria-hidden="true" /> The configuration can be deployed.
              </p>
            ) : (
              <p className="inline-status inline-status--danger">
                <AlertTriangle size={16} aria-hidden="true" /> The configuration cannot be deployed as it is.
              </p>
            )}
            {result.issues.length > 0 && (
              <ul className="plain-list stack">
                {result.issues.map((issue, i) => (
                  <li key={`${issue.field ?? 'general'}-${i}`}>
                    <strong>{issue.severity === 'error' ? 'Error' : 'Warning'}</strong>
                    {issue.field && <span className="mono"> ({issue.field})</span>}: {issue.message}
                  </li>
                ))}
              </ul>
            )}
            {result.adjustments.length > 0 && (
              <ul className="plain-list stack">
                {result.adjustments.map((a) => (
                  <li key={a.field}>
                    <strong>Will be adjusted</strong> <span className="mono">({a.field})</span>: {a.requested} → {a.effective}. {a.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

function ConfigurationForm({
  config,
  readOnly,
  onSaved,
  onReload,
}: {
  config: ApplicationConfiguration;
  readOnly: boolean;
  onSaved(config: ApplicationConfiguration): void;
  onReload(): void;
}) {
  const api = useApi();
  const demo = useDemoMode();
  const { notify } = useToast();
  const { application } = useApplication();
  const [baseline, setBaseline] = useState(() => toDraft(config));
  const [draft, setDraft] = useState(baseline);
  const [version, setVersion] = useState(config.version);
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const summaryRef = useRef<HTMLDivElement>(null);

  const clientErrors = useMemo(() => validate(draft), [draft]);
  const isDirty = comparable(draft) !== comparable(baseline);
  const { blocker } = useUnsavedChanges(isDirty && !readOnly);
  const save = useMutation((body: UpdateConfigurationRequest) => api.applications.updateConfiguration(application.id, body));
  const conflict = save.error?.status === 409;

  const errorFor = (key: string) => serverErrors[key] ?? (submitted ? (clientErrors[key] ?? null) : null);
  const update = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setServerErrors({});
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(clientErrors).length > 0) {
      summaryRef.current?.focus();
      return;
    }
    const result = await save.run({
      version,
      port: Number(draft.port),
      cpu: draft.cpu,
      memory: draft.memory,
      min_instances: Number(draft.min_instances),
      max_instances: Number(draft.max_instances),
      concurrency: draft.concurrency.trim() === '' ? null : Number(draft.concurrency),
      exposure: draft.exposure as Exposure,
      health_check_path: draft.health_check_path.trim() || null,
      environment: fromEnvRows(draft.environment),
      secret_refs: fromSecretRows(draft.secret_refs),
    });
    if (result.ok) {
      const next = toDraft(result.data);
      setBaseline(next);
      setDraft(next);
      setVersion(result.data.version);
      setSubmitted(false);
      onSaved(result.data);
      notify(simulated(messages.configurationSaved(), demo));
      return;
    }
    if (!result.error) return;
    const mapped: Errors = {};
    for (const fe of result.error.fieldErrors) mapped[fe.field] = fe.message;
    setServerErrors(mapped);
    requestAnimationFrame(() => summaryRef.current?.focus());
  };

  const clientMessages = submitted ? Object.values(clientErrors).filter((m): m is string => Boolean(m)) : [];
  const disabled = readOnly || save.isPending;
  const scalar = (key: keyof Draft & string) => ({
    value: draft[key] as string,
    onChange: (e: { target: { value: string } }) => update({ [key]: e.target.value }),
    error: errorFor(key),
    disabled,
  });

  return (
    <>
      <form className="form" onSubmit={onSubmit} noValidate aria-label="Application configuration">
        {readOnly && (
          <Alert tone="info" role="status">
            You can view this configuration but not change it. Ask an administrator for the developer role to edit it.
          </Alert>
        )}
        {conflict ? (
          <Alert
            tone="warning"
            title="The configuration changed since you opened it"
            role="alert"
            requestId={save.error?.requestId}
            actions={<Button size="sm" onClick={() => setConfirmDiscard(true)}>Load latest version</Button>}
          >
            Someone else saved a newer version. Your edits are still here; load the latest version to see their changes (your
            unsaved edits will be discarded), then make your changes again.
          </Alert>
        ) : (
          <FormErrorSummary ref={summaryRef} error={save.error} fieldMessages={save.error ? [] : clientMessages} />
        )}

        <Panel title="Runtime and scaling (requested)">
          <div className="form-grid form-grid--3">
            <TextField label="Container port" required type="number" inputMode="numeric" min={1} max={65535} {...scalar('port')} />
            <SelectField label="CPU (vCPU)" required {...scalar('cpu')}>
              {[...new Set([...CPU_OPTIONS, draft.cpu])].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </SelectField>
            <SelectField label="Memory" required {...scalar('memory')}>
              {[...new Set([...MEMORY_OPTIONS, draft.memory])].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </SelectField>
            <TextField label="Minimum instances" required type="number" inputMode="numeric" min={0} {...scalar('min_instances')} />
            <TextField label="Maximum instances" required type="number" inputMode="numeric" min={1} {...scalar('max_instances')} />
            <TextField label="Request concurrency" optional type="number" inputMode="numeric" min={1} placeholder="Provider default" {...scalar('concurrency')} />
            <SelectField label="Exposure" required {...scalar('exposure')}>
              <option value="public">{exposureLabel.public}</option>
              <option value="private">{exposureLabel.private}</option>
            </SelectField>
            <TextField label="Health-check path" optional mono placeholder="/healthz" {...scalar('health_check_path')} hint="Leave empty to use the provider's default TCP check." />
          </div>
        </Panel>

        <Panel title="Environment variables">
          <EnvironmentVariablesEditor rows={draft.environment} onChange={(environment) => update({ environment })} errorFor={errorFor} disabled={disabled} readOnly={readOnly} />
        </Panel>

        <Panel title="Secret references">
          <SecretReferencesEditor rows={draft.secret_refs} onChange={(secret_refs) => update({ secret_refs })} errorFor={errorFor} disabled={disabled} readOnly={readOnly} />
        </Panel>

        {!readOnly && (
          <div className="form-actions">
            <span className="form-actions__note text-muted text-small">
              {isDirty ? 'You have unsaved changes. Saved changes take effect on the next deployment.' : 'No unsaved changes.'}
            </span>
            <Button
              onClick={() => {
                setDraft(baseline);
                setServerErrors({});
                setSubmitted(false);
                save.reset();
              }}
              disabled={!isDirty || save.isPending}
              icon={<Undo2 size={16} aria-hidden="true" />}
            >
              Discard changes
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              loadingLabel="Saving…"
              disabled={!isDirty || (submitted && Object.keys(clientErrors).length > 0)}
              icon={<Save size={16} aria-hidden="true" />}
            >
              Save configuration
            </Button>
          </div>
        )}
        <UnsavedChangesDialog blocker={blocker} />
        <ConfirmDialog
          open={confirmDiscard}
          title="Load the latest configuration?"
          description="Your unsaved edits will be discarded and replaced with the version currently saved."
          confirmLabel="Discard my edits and reload"
          pendingLabel="Loading…"
          pending={false}
          error={null}
          onConfirm={() => {
            setConfirmDiscard(false);
            setDraft(baseline);
            onReload();
          }}
          onCancel={() => setConfirmDiscard(false)}
        />
      </form>
      <ValidationPanel dirty={isDirty} />
    </>
  );
}

export function ApplicationConfigurationTab() {
  const api = useApi();
  const { can } = useAuth();
  const { application } = useApplication();
  const config = useResource(`application:${application.id}:configuration`, (signal) => api.applications.getConfiguration(application.id, signal));
  const readOnly = !can('applications:update');

  if (config.error?.status === 403) return <AccessDeniedPage resource="this application's configuration" requestId={config.error.requestId} />;

  return (
    <div className="stack">
      {config.status === 'loading' && (
        <Panel>
          <CardSkeleton lines={6} label="Loading configuration" />
        </Panel>
      )}
      {config.status === 'error' && (
        <Panel>
          <ErrorState error={config.error} onRetry={config.reload} title="The configuration could not be loaded" />
        </Panel>
      )}
      {config.status === 'success' && config.data && (
        <>
          <RequestedVsEffective config={config.data} />
          <ConfigurationForm
            key={config.data.version}
            config={config.data}
            readOnly={readOnly}
            onSaved={(next) => config.setData(() => next)}
            onReload={config.reload}
          />
        </>
      )}
    </div>
  );
}
