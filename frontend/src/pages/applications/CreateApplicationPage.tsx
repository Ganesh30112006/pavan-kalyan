import { ArrowLeft, ArrowRight, CheckCircle2, Cloud, Pencil, Rocket } from 'lucide-react';
import { useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { RequirePermission } from '../../app/RequirePermission';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { Alert } from '../../components/feedback/Alert';
import { CapabilityFindings } from '../../components/feedback/CapabilityFindings';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button, ButtonLink } from '../../components/forms/Button';
import { EnvironmentVariablesEditor, SecretReferencesEditor } from '../../components/forms/EnvironmentEditor';
import { SelectField, TextAreaField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { useFormState } from '../../components/forms/useFormState';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { connectionStatus } from '../../components/status/statusMaps';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey';
import { useMutation } from '../../hooks/useMutation';
import { useResource, type Resource } from '../../hooks/useResource';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { APPLICATION_TYPES, RUNTIME_TYPES } from '../../services/api/decoders';
import type { CreateApplicationRequest, ProviderCapabilities, ProviderConnection, ProviderKind } from '../../types/api';
import { capabilityFindings } from '../../utils/capabilities';
import { validateRows, type EnvRow, type SecretRow } from '../../utils/environment';
import {
  applicationTypeDescription,
  applicationTypeLabel,
  exposureLabel,
  runtimeLabel,
} from '../../utils/labels';
import { messages, simulated } from '../../utils/messages';
import { CPU_OPTIONS, MEMORY_OPTIONS, PROVIDERS, PROVIDER_KINDS, providerLabel } from '../../utils/providers';
import { secretReferenceProvider } from '../../utils/validation';
import {
  ENVIRONMENT_STEP,
  FIELD_STEP,
  INITIAL_VALUES,
  REVIEW_STEP,
  SERVER_FIELDS,
  STEPS,
  TARGET_STEP,
  asProvider,
  buildValidators,
  crossFieldErrors,
  toRequest,
  toSpecToCheck,
  type WizardField,
  type WizardValues,
} from './create/wizardModel';
import { CostPreview } from './create/CostPreview';
import './applications.css';

/** "512Mi" → 0.5, "2Gi" → 2, "0.25" → 0.25: lets option lists from several providers be merged in order. */
function sizeValue(option: string): number {
  const mi = /^(\d+(?:\.\d+)?)Mi$/.exec(option);
  if (mi) return Number(mi[1]) / 1024;
  return Number.parseFloat(option);
}

function mergedOptions(fallback: readonly string[], lists: Array<string[] | undefined>): string[] {
  const known = lists.filter((l): l is string[] => Array.isArray(l));
  const all = new Set(known.length ? known.flat() : fallback);
  return [...all].sort((a, b) => sizeValue(a) - sizeValue(b));
}

function ReviewSection({ title, step, onEdit, children }: { title: string; step: number; onEdit(step: number): void; children: ReactNode }) {
  return (
    <section className="review-section" aria-label={title}>
      <div className="review-section__header">
        <h3>{title}</h3>
        <Button size="sm" variant="ghost" icon={<Pencil size={14} aria-hidden="true" />} onClick={() => onEdit(step)}>
          Edit <span className="visually-hidden">{title.toLowerCase()}</span>
        </Button>
      </div>
      <dl className="description-list">{children}</dl>
    </section>
  );
}

function CreateApplicationForm({
  connections,
  capabilities,
}: {
  connections: ProviderConnection[];
  capabilities: Record<ProviderKind, Resource<ProviderCapabilities>>;
}) {
  const api = useApi();
  const demo = useDemoMode();
  const navigate = useNavigate();
  const { notify } = useToast();
  const idempotencyKey = useIdempotencyKey();
  const summaryRef = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(0);
  const [unmatched, setUnmatched] = useState<string[]>([]);
  const [env, setEnv] = useState<EnvRow[]>([]);
  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  const [rowServerErrors, setRowServerErrors] = useState<Record<string, string>>({});

  const initial = useMemo((): WizardValues => {
    const usable = connections.filter((c) => c.status === 'connected');
    const only = usable.length === 1 ? usable[0] : undefined;
    return { ...INITIAL_VALUES, provider: only?.provider ?? '', connection_id: only?.id ?? '', region: only?.default_region ?? '' };
  }, [connections]);

  // Which validators apply depends on the source type and ref type, so they are rebuilt only when those change.
  const [shape, setShape] = useState({ source_type: initial.source_type, ref_type: initial.ref_type });
  const validators = useMemo(() => buildValidators({ ...INITIAL_VALUES, ...shape }), [shape]);
  const form = useFormState<WizardValues>(initial, validators);
  const { values } = form;
  const cross = crossFieldErrors(values);
  const rowErrors = useMemo(() => validateRows(env, secrets), [env, secrets]);
  const dirty = form.isDirty || env.length > 0 || secrets.length > 0;
  const { blocker, allowNavigation } = useUnsavedChanges(dirty);

  const create = useMutation((payload: CreateApplicationRequest, key: string) =>
    api.applications.create(payload, { idempotencyKey: key }),
  );

  const provider = asProvider(values.provider);
  const caps = provider ? capabilities[provider] : null;
  const findings = caps?.data ? capabilityFindings(toSpecToCheck(values, secrets.length), caps.data) : [];
  const blocking = findings.filter((f) => f.severity === 'error');
  const secretMismatches = provider
    ? secrets
        .filter((s) => {
          const target = secretReferenceProvider(s.reference);
          return target !== null && target !== provider;
        })
        .map((s) => `Secret ${s.name || '(unnamed)'} points to a secret manager that ${providerLabel(provider)} cannot read.`)
    : [];
  const providerConnections = connections.filter((c) => c.provider === provider);
  const selectedConnection = connections.find((c) => c.id === values.connection_id);
  const regions = caps?.data?.regions ?? (provider ? PROVIDERS[provider].regions : []);

  const stepErrors = (index: number): string[] => {
    const messagesForStep = (STEPS[index]?.fields ?? [])
      .map((f) => form.clientErrors[f] ?? cross[f])
      .filter((m): m is string => Boolean(m));
    if (index === ENVIRONMENT_STEP) messagesForStep.push(...Object.values(rowErrors));
    if (index === TARGET_STEP) messagesForStep.push(...blocking.map((f) => f.message), ...secretMismatches);
    return messagesForStep;
  };

  const errorFor = (name: WizardField) => form.errorFor(name) ?? (form.submitted ? (cross[name] ?? null) : null);
  const rowErrorFor = (key: string) => rowServerErrors[key] ?? (form.submitted ? (rowErrors[key] ?? null) : null);

  const goTo = (index: number) => {
    form.setSubmitted(false);
    setStep(index);
  };

  const goNext = () => {
    form.setSubmitted(true);
    if (stepErrors(step).length > 0) {
      summaryRef.current?.focus();
      return;
    }
    goTo(Math.min(step + 1, REVIEW_STEP));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (step < REVIEW_STEP) {
      goNext();
      return;
    }
    const firstInvalid = STEPS.findIndex((_, i) => stepErrors(i).length > 0);
    if (firstInvalid >= 0) {
      setStep(firstInvalid);
      form.setSubmitted(true);
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    const payload = toRequest(values, env, secrets);
    const result = await create.run(payload, idempotencyKey(payload));
    if (result.ok) {
      notify(simulated(messages.applicationCreated(result.data.name), demo));
      allowNavigation();
      navigate(`/applications/${encodeURIComponent(result.data.id)}`);
      return;
    }
    if (!result.error) return;
    // Row errors (environment.0.name, secret_refs.1.reference) are shown on their rows, not in the summary list.
    const rowMatched: Record<string, string> = {};
    for (const fe of result.error.fieldErrors) {
      if (/^(environment|secret_refs)\.\d+\.(name|value|reference)$/.test(fe.field)) rowMatched[fe.field] = fe.message;
    }
    const rowMessages = new Set(Object.values(rowMatched));
    const rest = form.applyServerErrors(result.error, SERVER_FIELDS);
    setRowServerErrors(rowMatched);
    setUnmatched(rest.filter((m) => !rowMessages.has(m)));
    const errorStep = Object.keys(rowMatched).length
      ? ENVIRONMENT_STEP
      : STEPS.findIndex((s) => s.fields.some((f) => result.error?.fieldErrors.some((fe) => (SERVER_FIELDS[fe.field] ?? fe.field) === f)));
    if (errorStep >= 0) setStep(errorStep);
    requestAnimationFrame(() => summaryRef.current?.focus());
  };

  const field = (name: WizardField) => ({
    value: values[name],
    onChange: (e: { target: { value: string } }) => form.setValue(name, e.target.value),
    onBlur: () => form.touch(name),
    error: errorFor(name),
    disabled: create.isPending,
  });

  const setProvider = (next: ProviderKind) => {
    form.setValue('provider', next);
    const usable = connections.filter((c) => c.provider === next && c.status === 'connected');
    const only = usable.length === 1 ? usable[0] : undefined;
    form.setValue('connection_id', only?.id ?? '');
    form.setValue('region', only?.default_region ?? '');
  };

  const setConnection = (id: string) => {
    form.setValue('connection_id', id);
    const conn = connections.find((c) => c.id === id);
    if (conn) form.setValue('region', conn.default_region);
  };

  /** Per-provider facts from the capabilities the backend returned, e.g. "AWS App Runner: up to 200". */
  const providerHint = (describe: (c: ProviderCapabilities) => string): string | undefined => {
    const parts = PROVIDER_KINDS.map((p) => capabilities[p].data)
      .filter((c): c is ProviderCapabilities => Boolean(c))
      .map((c) => `${PROVIDERS[c.provider].shortLabel}: ${describe(c)}`);
    return parts.length ? parts.join(' · ') : undefined;
  };

  const cpuOptions = mergedOptions(CPU_OPTIONS, PROVIDER_KINDS.map((p) => capabilities[p].data?.cpu_options));
  const memoryOptions = mergedOptions(MEMORY_OPTIONS, PROVIDER_KINDS.map((p) => capabilities[p].data?.memory_options));
  const visibleErrors = form.submitted ? stepErrors(step) : [];
  const nonBlockingFindings = findings.filter((f) => f.severity !== 'error');

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="Create application">
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li
            key={s.title}
            className={`wizard-steps__item${i < step ? ' wizard-steps__item--done' : ''}`}
            aria-current={i === step ? 'step' : undefined}
          >
            <span className="wizard-steps__label">{s.title}</span>
            {i < step && <span className="visually-hidden"> (completed)</span>}
          </li>
        ))}
      </ol>

      <FormErrorSummary
        ref={summaryRef}
        error={create.error}
        fieldMessages={[...visibleErrors, ...unmatched]}
        onCheckStatus={() => navigate(`/applications?q=${encodeURIComponent(values.name)}`)}
      />
      {create.error && (create.error.kind === 'timeout' || create.error.kind === 'network') && (
        <Alert tone="info">
          Submitting again with the same values is safe: CloudBridge recognizes a repeated request and will not create a
          second application.
        </Alert>
      )}

      <Panel title={`Step ${step + 1} of ${STEPS.length}: ${STEPS[step]?.title}`}>
        {step === 0 && (
          <div className="form">
            <div className="form-grid">
              <TextField
                label="Application name"
                required
                autoComplete="off"
                spellCheck={false}
                mono
                hint="Lowercase letters, numbers and hyphens, 3–40 characters. Used as the service name on the provider."
                {...field('name')}
              />
              <TextAreaField label="Description" optional rows={2} {...field('description')} />
            </div>
            <fieldset className="fieldset">
              <legend>Application type</legend>
              <div className="radio-cards">
                {APPLICATION_TYPES.map((t) => (
                  <label key={t} className="radio-card">
                    <input
                      type="radio"
                      name="application_type"
                      value={t}
                      checked={values.application_type === t}
                      onChange={() => form.setValue('application_type', t)}
                      disabled={create.isPending}
                    />
                    <span className="radio-card__text">
                      <strong>{applicationTypeLabel[t]}</strong>
                      <span className="field__hint">{applicationTypeDescription[t]}</span>
                    </span>
                  </label>
                ))}
              </div>
              {providerHint((c) => c.supported_application_types.map((t) => applicationTypeLabel[t]).join(', ')) && (
                <p className="field__hint">Supported: {providerHint((c) => c.supported_application_types.map((t) => applicationTypeLabel[t].toLowerCase()).join(', '))}</p>
              )}
            </fieldset>
          </div>
        )}

        {step === 1 && (
          <div className="form">
            <fieldset className="fieldset">
              <legend>Source</legend>
              <div className="radio-cards">
                {(
                  [
                    ['repository', 'Git repository', 'CloudBridge builds an image from a Dockerfile in the repository.'],
                    ['image', 'Container image', 'Deploy an image that is already in a registry.'],
                  ] as const
                ).map(([value, label, description]) => (
                  <label key={value} className="radio-card">
                    <input
                      type="radio"
                      name="source_type"
                      value={value}
                      checked={values.source_type === value}
                      onChange={() => {
                        form.setValue('source_type', value);
                        setShape((s) => ({ ...s, source_type: value }));
                      }}
                      disabled={create.isPending}
                    />
                    <span className="radio-card__text">
                      <strong>{label}</strong>
                      <span className="field__hint">{description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            {values.source_type === 'image' ? (
              <TextField
                label="Image reference"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="123456789012.dkr.ecr.eu-west-1.amazonaws.com/app@sha256:…"
                hint="Registry, repository and tag or digest. A digest (@sha256:…) pins an immutable image."
                {...field('image')}
              />
            ) : (
              <div className="form-grid">
                <TextField
                  label="Repository URL"
                  required
                  type="url"
                  mono
                  spellCheck={false}
                  autoComplete="off"
                  className="form-grid__full"
                  placeholder="https://github.com/org/repo.git"
                  hint="HTTPS only, without credentials. Repository access is configured on the backend."
                  {...field('repository_url')}
                />
                <SelectField
                  label="Reference type"
                  required
                  {...field('ref_type')}
                  onChange={(e) => {
                    form.setValue('ref_type', e.target.value);
                    form.setValue('git_ref', e.target.value === 'branch' ? 'main' : '');
                    setShape((s) => ({ ...s, ref_type: e.target.value }));
                  }}
                  hint="A commit SHA always builds the same code; a branch builds its latest commit at deploy time."
                >
                  <option value="branch">Branch</option>
                  <option value="commit">Commit (immutable)</option>
                </SelectField>
                <TextField
                  label={values.ref_type === 'commit' ? 'Commit SHA' : 'Branch'}
                  required
                  mono
                  spellCheck={false}
                  autoComplete="off"
                  {...field('git_ref')}
                />
                <TextField label="Dockerfile path" required mono spellCheck={false} hint="Relative to the repository root." {...field('dockerfile_path')} />
                <TextField label="Build context" required mono spellCheck={false} hint="Directory sent to the build. Use . for the repository root." {...field('build_context')} />
              </div>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="form-grid form-grid--3">
            <SelectField label="Runtime" required {...field('runtime')} hint="The language or framework inside the container. Used for display and build defaults.">
              {RUNTIME_TYPES.map((r) => (
                <option key={r} value={r}>
                  {runtimeLabel[r]}
                </option>
              ))}
            </SelectField>
            <TextField label="Container port" required type="number" inputMode="numeric" min={1} max={65535} {...field('port')} hint="The port your process listens on." />
            <TextField label="Health-check path" optional mono placeholder="/healthz" {...field('health_check_path')} hint="Leave empty to use the provider's default TCP check." />
            <SelectField label="CPU (vCPU)" required {...field('cpu')} hint={providerHint((c) => c.cpu_options.join(', '))}>
              {cpuOptions.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </SelectField>
            <SelectField label="Memory" required {...field('memory')} hint={providerHint((c) => `${c.memory_options[0] ?? '?'}–${c.memory_options[c.memory_options.length - 1] ?? '?'}`)}>
              {memoryOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </SelectField>
          </div>
        )}

        {step === 3 && (
          <div className="form">
            <div className="form-grid form-grid--3">
              <TextField
                label="Minimum instances"
                required
                type="number"
                inputMode="numeric"
                min={0}
                {...field('min_instances')}
                hint={providerHint((c) => (c.min_instances_floor === 0 ? 'can scale to zero' : `at least ${c.min_instances_floor}`))}
              />
              <TextField
                label="Maximum instances"
                required
                type="number"
                inputMode="numeric"
                min={1}
                {...field('max_instances')}
                hint={providerHint((c) => `up to ${c.max_instances_limit}`)}
              />
              <TextField
                label="Request concurrency"
                optional
                type="number"
                inputMode="numeric"
                min={1}
                placeholder="Provider default"
                {...field('concurrency')}
                hint={providerHint((c) => (c.supports_concurrency ? `up to ${c.max_concurrency ?? 'no limit'}` : 'not supported'))}
              />
            </div>
            <fieldset className="fieldset">
              <legend>Exposure</legend>
              <div className="radio-cards">
                {(
                  [
                    ['public', 'Anyone on the internet can reach the service URL.'],
                    ['private', 'Reachable only from your private network.'],
                  ] as const
                ).map(([value, description]) => (
                  <label key={value} className="radio-card">
                    <input
                      type="radio"
                      name="exposure"
                      value={value}
                      checked={values.exposure === value}
                      onChange={() => form.setValue('exposure', value)}
                      disabled={create.isPending}
                    />
                    <span className="radio-card__text">
                      <strong>{exposureLabel[value]}</strong>
                      <span className="field__hint">{description}</span>
                    </span>
                  </label>
                ))}
              </div>
              {providerHint((c) => (c.supports_private_exposure ? 'private supported' : 'public only')) && (
                <p className="field__hint">{providerHint((c) => (c.supports_private_exposure ? 'private supported' : 'public only'))}</p>
              )}
            </fieldset>
          </div>
        )}

        {step === ENVIRONMENT_STEP && (
          <div className="form">
            <section className="stack" aria-labelledby="env-heading">
              <h3 id="env-heading">Environment variables</h3>
              <EnvironmentVariablesEditor rows={env} onChange={setEnv} errorFor={rowErrorFor} disabled={create.isPending} />
            </section>
            <section className="stack" aria-labelledby="secrets-heading">
              <h3 id="secrets-heading">Secret references</h3>
              <SecretReferencesEditor rows={secrets} onChange={setSecrets} errorFor={rowErrorFor} disabled={create.isPending} />
            </section>
          </div>
        )}

        {step === TARGET_STEP && (
          <div className="form">
            <fieldset className="fieldset">
              <legend>Cloud provider</legend>
              <div className="radio-cards">
                {PROVIDER_KINDS.map((p) => {
                  const ready = connections.filter((c) => c.provider === p && c.status === 'connected').length;
                  return (
                    <label key={p} className="radio-card">
                      <input
                        type="radio"
                        name="provider"
                        value={p}
                        checked={values.provider === p}
                        onChange={() => setProvider(p)}
                        disabled={create.isPending}
                      />
                      <span className="radio-card__text">
                        <strong>{PROVIDERS[p].label}</strong>
                        <span className="field__hint">
                          {ready > 0 ? `${ready} validated connection${ready === 1 ? '' : 's'}` : 'No validated connection'}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
              {errorFor('provider') && <span className="field__error">{errorFor('provider')}</span>}
            </fieldset>

            {provider && (
              <>
                <div className="form-grid">
                  <SelectField
                    label="Cloud account"
                    required
                    {...field('connection_id')}
                    onChange={(e) => setConnection(e.target.value)}
                    hint={
                      providerConnections.some((c) => c.status === 'connected') ? (
                        'Only validated connections can be selected.'
                      ) : (
                        <>
                          No validated {providerLabel(provider)} connection. <Link to="/providers/new">Connect one</Link> or choose
                          another provider.
                        </>
                      )
                    }
                  >
                    <option value="">Select a connection</option>
                    {providerConnections.map((c) => (
                      <option key={c.id} value={c.id} disabled={c.status !== 'connected'}>
                        {c.name}
                        {c.status !== 'connected' ? ` (${connectionStatus[c.status].label})` : ''}
                      </option>
                    ))}
                  </SelectField>
                  <SelectField
                    label="Region"
                    required
                    {...field('region')}
                    disabled={!selectedConnection || create.isPending}
                    hint={caps?.data ? `Regions where ${providerLabel(provider)} is available.` : undefined}
                  >
                    <option value="">{selectedConnection ? 'Select a region' : 'Select a connection first'}</option>
                    {[...new Set([...regions, ...(values.region ? [values.region] : [])])].map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </SelectField>
                </div>

                <section className="stack" aria-labelledby="capabilities-heading">
                  <h3 id="capabilities-heading">Provider compatibility</h3>
                  {caps?.status === 'loading' && <CardSkeleton lines={2} label="Loading provider capabilities" />}
                  {caps?.status === 'error' && (
                    <Alert tone="warning" title="Capabilities could not be loaded" actions={<Button size="sm" onClick={caps.reload}>Retry</Button>}>
                      The settings could not be checked against {providerLabel(provider)} here. You can continue; CloudBridge
                      validates them when you create the application.
                    </Alert>
                  )}
                  {caps?.data && findings.length === 0 && secretMismatches.length === 0 && (
                    <p className="inline-status inline-status--success">
                      <CheckCircle2 size={16} aria-hidden="true" /> The configuration is within {providerLabel(provider)}'s
                      reported limits.
                    </p>
                  )}
                  <CapabilityFindings findings={findings} onEdit={(f) => goTo(FIELD_STEP[f])} />
                  {secretMismatches.map((m) => (
                    <Alert key={m} tone="danger">
                      {m}{' '}
                      <button type="button" className="link-button" onClick={() => goTo(ENVIRONMENT_STEP)}>
                        Edit secret references
                      </button>
                    </Alert>
                  ))}
                  {caps?.data && caps.data.notes.length > 0 && (
                    <ul className="text-muted text-small notes-list">
                      {caps.data.notes.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  )}
                </section>
              </>
            )}
          </div>
        )}

        {step === REVIEW_STEP && (
          <div className="stack">
            <p className="text-muted">
              Check the configuration. Creating the application registers it with CloudBridge; it is not deployed until you
              start a deployment from the application page.
            </p>
            {nonBlockingFindings.length > 0 && (
              <Alert tone="warning" title={`${providerLabel(provider ?? 'aws_app_runner')} will change some settings`}>
                <CapabilityFindings findings={nonBlockingFindings} onEdit={(f) => goTo(FIELD_STEP[f])} />
              </Alert>
            )}
            {caps?.status === 'error' && (
              <Alert tone="info">Provider capabilities were not available, so settings could not be checked before submitting.</Alert>
            )}
            {provider && (
              <CostPreview
                request={{
                  provider,
                  cpu: values.cpu,
                  memory: values.memory,
                  min_instances: Number(values.min_instances) || 0,
                  max_instances: Number(values.max_instances) || 1,
                }}
              />
            )}
            <ReviewSection title="Details" step={0} onEdit={goTo}>
              <dt>Name</dt>
              <dd className="mono">{values.name}</dd>
              <dt>Description</dt>
              <dd>{values.description || '—'}</dd>
              <dt>Type</dt>
              <dd>{applicationTypeLabel[values.application_type as keyof typeof applicationTypeLabel]}</dd>
            </ReviewSection>
            <ReviewSection title="Source" step={1} onEdit={goTo}>
              {values.source_type === 'image' ? (
                <>
                  <dt>Image</dt>
                  <dd className="mono">{values.image}</dd>
                </>
              ) : (
                <>
                  <dt>Repository</dt>
                  <dd className="mono">{values.repository_url}</dd>
                  <dt>{values.ref_type === 'commit' ? 'Commit' : 'Branch'}</dt>
                  <dd className="mono">{values.git_ref}</dd>
                  <dt>Dockerfile</dt>
                  <dd className="mono">{values.dockerfile_path}</dd>
                  <dt>Build context</dt>
                  <dd className="mono">{values.build_context}</dd>
                </>
              )}
            </ReviewSection>
            <ReviewSection title="Runtime" step={2} onEdit={goTo}>
              <dt>Runtime</dt>
              <dd>{runtimeLabel[values.runtime as keyof typeof runtimeLabel]}</dd>
              <dt>Port</dt>
              <dd>{values.port}</dd>
              <dt>Health check</dt>
              <dd className="mono">{values.health_check_path || 'Provider default (TCP)'}</dd>
              <dt>Resources</dt>
              <dd>
                {values.cpu} vCPU · {values.memory}
              </dd>
            </ReviewSection>
            <ReviewSection title="Scaling and exposure" step={3} onEdit={goTo}>
              <dt>Instances</dt>
              <dd>
                {values.min_instances}–{values.max_instances}
              </dd>
              <dt>Concurrency</dt>
              <dd>{values.concurrency || 'Provider default'}</dd>
              <dt>Exposure</dt>
              <dd>{exposureLabel[values.exposure as keyof typeof exposureLabel]}</dd>
            </ReviewSection>
            <ReviewSection title="Environment" step={ENVIRONMENT_STEP} onEdit={goTo}>
              <dt>Variables</dt>
              <dd>
                {env.length === 0 ? (
                  '—'
                ) : (
                  <ul className="plain-list mono">
                    {env.map((r) => (
                      <li key={r.key}>
                        {r.name}={r.value}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt>Secret references</dt>
              <dd>
                {secrets.length === 0 ? (
                  '—'
                ) : (
                  <ul className="plain-list mono">
                    {secrets.map((r) => (
                      <li key={r.key}>
                        {r.name} → {r.reference}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
            </ReviewSection>
            <ReviewSection title="Deployment target" step={TARGET_STEP} onEdit={goTo}>
              <dt>Provider</dt>
              <dd>{provider ? providerLabel(provider) : '—'}</dd>
              <dt>Account</dt>
              <dd>{selectedConnection?.name ?? '—'}</dd>
              <dt>Region</dt>
              <dd>{values.region || '—'}</dd>
            </ReviewSection>
          </div>
        )}
      </Panel>

      <div className="form-actions form-actions--split">
        <div className="cluster">
          <ButtonLink to="/applications">Cancel</ButtonLink>
        </div>
        <div className="cluster">
          {step > 0 && (
            <Button onClick={() => goTo(step - 1)} disabled={create.isPending} icon={<ArrowLeft size={16} aria-hidden="true" />}>
              Back
            </Button>
          )}
          {step < REVIEW_STEP ? (
            <Button variant="primary" type="submit" icon={<ArrowRight size={16} aria-hidden="true" />}>
              Next
            </Button>
          ) : (
            <Button variant="primary" type="submit" loading={create.isPending} loadingLabel="Creating…" icon={<Rocket size={16} aria-hidden="true" />}>
              Create application
            </Button>
          )}
        </div>
      </div>
      <UnsavedChangesDialog blocker={blocker} />
    </form>
  );
}

export function CreateApplicationPage() {
  const api = useApi();
  const connections = useResource('create-app:connections', (signal) => api.providers.list(signal));
  const aws = useResource('capabilities:aws_app_runner', (signal) => api.providers.getCapabilities('aws_app_runner', signal));
  const gcp = useResource('capabilities:gcp_cloud_run', (signal) => api.providers.getCapabilities('gcp_cloud_run', signal));
  const azure = useResource('capabilities:azure_container_apps', (signal) =>
    api.providers.getCapabilities('azure_container_apps', signal),
  );
  const ecs = useResource('capabilities:aws_ecs_express', (signal) => api.providers.getCapabilities('aws_ecs_express', signal));
  const kubernetes = useResource('capabilities:kubernetes', (signal) => api.providers.getCapabilities('kubernetes', signal));
  const capabilities = {
    aws_app_runner: aws,
    gcp_cloud_run: gcp,
    azure_container_apps: azure,
    aws_ecs_express: ecs,
    kubernetes,
  };

  return (
    <div className="page">
      <PageHeader
        title="Create application"
        description="Describe a containerized application once; CloudBridge maps it onto the provider you choose."
        breadcrumbs={[
          { label: 'Overview', to: '/' },
          { label: 'Applications', to: '/applications' },
          { label: 'Create' },
        ]}
      />
      <RequirePermission permission="applications:create" resource="application creation">
        {connections.status === 'loading' && (
          <Panel>
            <CardSkeleton lines={4} label="Loading provider connections" />
          </Panel>
        )}
        {connections.status === 'error' && (
          <Panel>
            <ErrorState error={connections.error} title="Provider connections could not be loaded" onRetry={connections.reload} />
          </Panel>
        )}
        {connections.status === 'success' && connections.data && !connections.data.some((c) => c.status === 'connected') && (
          <Panel>
            <EmptyState
              icon={Cloud}
              title="Connect a provider first"
              message={
                connections.data.length === 0
                  ? 'Applications are deployed through a provider connection. Add an AWS App Runner or Google Cloud Run connection to continue.'
                  : 'None of your provider connections are validated. Validate a connection to continue.'
              }
              actions={
                <ButtonLink variant="primary" to={connections.data.length === 0 ? '/providers/new' : '/providers'}>
                  {connections.data.length === 0 ? 'Connect a provider' : 'Review connections'}
                </ButtonLink>
              }
            />
          </Panel>
        )}
        {connections.status === 'success' && connections.data?.some((c) => c.status === 'connected') && (
          <CreateApplicationForm connections={connections.data} capabilities={capabilities} />
        )}
      </RequirePermission>
    </div>
  );
}
