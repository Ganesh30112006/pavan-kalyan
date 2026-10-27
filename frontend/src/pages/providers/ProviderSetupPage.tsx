import { Cloud, ShieldCheck } from 'lucide-react';
import { useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { RequirePermission } from '../../app/RequirePermission';
import { UnsavedChangesDialog } from '../../components/dialogs/UnsavedChangesDialog';
import { Alert } from '../../components/feedback/Alert';
import { Button, ButtonLink } from '../../components/forms/Button';
import { SelectField, TextAreaField, TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { useFormState } from '../../components/forms/useFormState';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { useApi, useDemoMode } from '../../hooks/useApi';
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey';
import { useMutation } from '../../hooks/useMutation';
import { useToast } from '../../hooks/useToast';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { useResource } from '../../hooks/useResource';
import type { ConnectionSetup, CreateConnectionRequest, ProviderKind } from '../../types/api';
import { messages, simulated } from '../../utils/messages';
import { PROVIDERS, PROVIDER_KINDS } from '../../utils/providers';
import {
  awsAccountId,
  awsRoleArn,
  azureEnvironment,
  azureGuid,
  azureRegistry,
  azureResourceGroup,
  compose,
  ecsCluster,
  kubernetesApiServer,
  kubernetesNamespace,
  pemCertificate,
  gcpProjectId,
  gcpServiceAccount,
  gcpWorkloadIdentityProvider,
  maxLength,
  required,
  type Validator,
} from '../../utils/validation';
import { ProviderPermissions } from './ProviderPermissions';
import { ScriptSetup } from './ScriptSetup';
import { NEW_CONNECTION_DEFAULTS, newConnectionCommand } from './setupCommand';
import './providers.css';

const INITIAL = {
  name: '',
  default_region: '',
  account_id: '',
  role_arn: '',
  project_id: '',
  workload_identity_provider: '',
  service_account_email: '',
  tenant_id: '',
  subscription_id: '',
  client_id: '',
  resource_group: '',
  environment_name: '',
  registry_name: '',
  ecs_cluster: '',
  api_server: '',
  ca_certificate: '',
  namespace: '',
  kubernetes_auth: 'aks',
  audience: '',
};

type Values = typeof INITIAL;

/** Fields the setup script's link may fill in. All are non-secret identifiers; the form still validates them. */
const PREFILL: ReadonlyArray<keyof Values> = [
  'default_region',
  'tenant_id',
  'subscription_id',
  'client_id',
  'resource_group',
  'environment_name',
  'registry_name',
  'api_server',
  'ca_certificate',
  'namespace',
];

function prefilled(params: URLSearchParams): Values {
  const values = { ...INITIAL };
  for (const key of PREFILL) {
    const value = params.get(key);
    if (value) values[key] = value.trim().slice(0, 16_384);
  }
  return values;
}

const SERVER_FIELDS: Record<string, keyof Values> = {
  'aws.account_id': 'account_id',
  'aws.role_arn': 'role_arn',
  'gcp.project_id': 'project_id',
  'gcp.workload_identity_provider': 'workload_identity_provider',
  'gcp.service_account_email': 'service_account_email',
  'azure.tenant_id': 'tenant_id',
  'azure.subscription_id': 'subscription_id',
  'azure.client_id': 'client_id',
  'azure.resource_group': 'resource_group',
  'azure.environment_name': 'environment_name',
  'azure.registry_name': 'registry_name',
  'ecs.account_id': 'account_id',
  'ecs.role_arn': 'role_arn',
  'ecs.cluster': 'ecs_cluster',
  'kubernetes.api_server': 'api_server',
  'kubernetes.ca_certificate': 'ca_certificate',
  'kubernetes.namespace': 'namespace',
  'kubernetes.tenant_id': 'tenant_id',
  'kubernetes.client_id': 'client_id',
  'kubernetes.audience': 'audience',
};

const NO_KEYS: Record<ProviderKind, string> = {
  aws_app_runner: 'CloudBridge assumes the IAM role with a unique external ID. Do not create or paste access keys.',
  gcp_cloud_run: 'CloudBridge authenticates through workload identity federation. Do not create or paste service account keys.',
  azure_container_apps:
    'CloudBridge signs in to your app registration through a federated credential. Do not create or paste client secrets or certificates.',
  aws_ecs_express: 'CloudBridge assumes the IAM role with a unique external ID. Do not create or paste access keys.',
  kubernetes:
    'CloudBridge presents a short-lived signed token (through Microsoft Entra ID for AKS). Do not paste a kubeconfig, service account token or client certificate.',
};

const PROVIDER_HINTS: Record<ProviderKind, string> = {
  aws_app_runner: 'Uses an IAM role with an external ID.',
  gcp_cloud_run: 'Uses workload identity federation.',
  azure_container_apps: 'Uses an app registration with a federated credential.',
  aws_ecs_express: 'For AWS accounts without App Runner. Uses an IAM role with an external ID.',
  kubernetes: 'An existing cluster (AKS or any cluster that trusts CloudBridge’s OIDC token).',
};

/** Azure and AKS: the script creates the app registration and prints a link that fills in this form. */
function NewConnectionScript({ provider, setup, values }: { provider: ProviderKind; setup: ConnectionSetup | undefined; values: Values }) {
  if (!setup) return <p className="text-muted text-small">Loading…</p>;
  const origin = `${window.location.origin}${import.meta.env.BASE_URL.replace(/\/$/, '')}`;
  const command = newConnectionCommand(provider, setup, values, origin);
  if (!command) {
    if (!setup.federation_issuer || !setup.trusted_principal) {
      return (
        <p className="text-small">
          CloudBridge is not set up to sign in to Azure yet (no federation issuer is configured). Ask your CloudBridge
          administrator, or follow the steps after saving.
        </p>
      );
    }
    return <p className="text-small">Choose the default region below to get the command.</p>;
  }
  const azure = provider === 'azure_container_apps';
  return (
    <div className="stack text-small">
      <p>
        No app registration yet? This script creates it with a federated credential (no secret),{' '}
        {azure
          ? `and the resource group (${values.resource_group || NEW_CONNECTION_DEFAULTS.resource_group}), Container Apps environment (${values.environment_name || NEW_CONNECTION_DEFAULTS.environment_name}) and registry you name below if they do not exist`
          : 'grants it access to the namespace'}
        . The command updates as you fill in the form.
        {!azure && !values.api_server && ' Replace CLUSTER_NAME and CLUSTER_RESOURCE_GROUP with your cluster’s.'}
      </p>
      <ScriptSetup
        shell="azure"
        command={command}
        after="Open the link it prints at the end: it fills in this form. Then select “Save and continue”."
      />
    </div>
  );
}

function SetupForm({ provider }: { provider: ProviderKind }) {
  const api = useApi();
  const demo = useDemoMode();
  const { notify } = useToast();
  const navigate = useNavigate();
  const idempotencyKey = useIdempotencyKey();
  const summaryRef = useRef<HTMLDivElement>(null);
  const [unmatched, setUnmatched] = useState<string[]>([]);
  const isAws = provider === 'aws_app_runner' || provider === 'aws_ecs_express';
  const isEcs = provider === 'aws_ecs_express';
  const isAzure = provider === 'azure_container_apps';
  const isKubernetes = provider === 'kubernetes';

  const [params] = useSearchParams();
  const [initial] = useState(() => prefilled(params));
  const [k8sAuth, setK8sAuth] = useState<'aks' | 'oidc'>('aks');
  const scripted = isAzure || (isKubernetes && k8sAuth === 'aks');
  const setup = useResource(`provider-setup:${provider}`, (signal) => api.providers.getSetup(provider, signal), {
    enabled: isAzure || isKubernetes,
  });
  const validators = useMemo((): Partial<Record<keyof Values, Validator>> => {
    const common = {
      name: compose(required('Connection name'), maxLength('Connection name', 80)),
      default_region: required('Default region'),
    };
    if (isAws) {
      return {
        ...common,
        account_id: awsAccountId,
        role_arn: awsRoleArn,
        ...(isEcs ? { ecs_cluster: ecsCluster } : {}),
      };
    }
    if (isKubernetes) {
      return {
        ...common,
        api_server: kubernetesApiServer,
        ca_certificate: pemCertificate,
        namespace: kubernetesNamespace,
        ...(k8sAuth === 'aks'
          ? { tenant_id: azureGuid('Tenant ID'), client_id: azureGuid('Application (client) ID') }
          : { audience: required('Audience') }),
      };
    }
    if (isAzure) {
      return {
        ...common,
        tenant_id: azureGuid('Tenant ID'),
        subscription_id: azureGuid('Subscription ID'),
        client_id: azureGuid('Application (client) ID'),
        resource_group: azureResourceGroup,
        environment_name: azureEnvironment,
        registry_name: azureRegistry,
      };
    }
    return {
      ...common,
      project_id: gcpProjectId,
      workload_identity_provider: gcpWorkloadIdentityProvider,
      service_account_email: gcpServiceAccount,
    };
  }, [isAws, isAzure, isEcs, isKubernetes, k8sAuth]);

  const form = useFormState(initial, validators);
  const { values } = form;
  const roleAccount = /^arn:aws(?:-[a-z]+)*:iam::(\d{12}):/.exec(values.role_arn.trim())?.[1];
  const accountMismatch =
    isAws && roleAccount && /^\d{12}$/.test(values.account_id.trim()) && roleAccount !== values.account_id.trim()
      ? 'The role ARN belongs to a different account than the account ID above.'
      : null;
  const { blocker, allowNavigation } = useUnsavedChanges(form.isDirty);
  const create = useMutation((payload: CreateConnectionRequest, key: string) => api.providers.create(payload, { idempotencyKey: key }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    form.setSubmitted(true);
    if (!form.isValid || accountMismatch) {
      summaryRef.current?.focus();
      return;
    }
    const payload: CreateConnectionRequest = {
      name: values.name.trim(),
      provider,
      default_region: values.default_region,
      ...(isEcs
        ? {
            ecs: {
              account_id: values.account_id.trim(),
              role_arn: values.role_arn.trim(),
              cluster: values.ecs_cluster.trim() || null,
            },
          }
        : isAws
        ? { aws: { account_id: values.account_id.trim(), role_arn: values.role_arn.trim() } }
        : isKubernetes
        ? {
            kubernetes: {
              api_server: values.api_server.trim(),
              ca_certificate: values.ca_certificate.trim(),
              namespace: values.namespace.trim(),
              auth: k8sAuth,
              ...(k8sAuth === 'aks'
                ? { tenant_id: values.tenant_id.trim(), client_id: values.client_id.trim() }
                : { audience: values.audience.trim() }),
            },
          }
        : isAzure
        ? {
            azure: {
              tenant_id: values.tenant_id.trim(),
              subscription_id: values.subscription_id.trim(),
              client_id: values.client_id.trim(),
              resource_group: values.resource_group.trim(),
              environment_name: values.environment_name.trim(),
              registry_name: values.registry_name.trim() || null,
            },
          }
        : {
            gcp: {
              project_id: values.project_id.trim(),
              workload_identity_provider: values.workload_identity_provider.trim(),
              service_account_email: values.service_account_email.trim(),
            },
          }),
    };
    const result = await create.run(payload, idempotencyKey(payload));
    if (result.ok) {
      notify(simulated(messages.providerCreated(result.data.name), demo));
      allowNavigation();
      navigate(`/providers/${encodeURIComponent(result.data.id)}`);
      return;
    }
    if (!result.error) return;
    setUnmatched(form.applyServerErrors(result.error, SERVER_FIELDS));
    requestAnimationFrame(() => summaryRef.current?.focus());
  };

  const field = (name: keyof Values) => ({
    value: values[name],
    onChange: (e: { target: { value: string } }) => form.setValue(name, e.target.value),
    onBlur: () => form.touch(name),
    error: form.errorFor(name),
    disabled: create.isPending,
  });

  const clientMessages = form.submitted
    ? [...Object.values(form.clientErrors).filter((m): m is string => Boolean(m)), ...(accountMismatch ? [accountMismatch] : [])]
    : [];

  return (
    <form className="form" onSubmit={submit} noValidate aria-label={`Connect ${PROVIDERS[provider].label}`}>
      <FormErrorSummary
        ref={summaryRef}
        error={create.error}
        fieldMessages={[...(create.error ? [] : clientMessages), ...unmatched]}
        onCheckStatus={() => navigate('/providers')}
      />
      {scripted && (
        <Panel title="Start with the setup script (optional)">
          <NewConnectionScript provider={provider} setup={setup.data} values={values} />
        </Panel>
      )}
      <Panel title="Connection details">
        <div className="form-grid">
          <TextField label="Connection name" required hint="A label for your team, e.g. “Production AWS”." autoComplete="off" {...field('name')} />
          <SelectField label="Default region" required {...field('default_region')}>
            <option value="">Select a region</option>
            {PROVIDERS[provider].regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </SelectField>
          {isAws ? (
            <>
              <TextField label="AWS account ID" required inputMode="numeric" mono autoComplete="off" placeholder="123456789012" {...field('account_id')} />
              <TextField
                label="IAM role ARN"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="arn:aws:iam::123456789012:role/CloudBridgeDeploy"
                hint="The role CloudBridge assumes, e.g. role/CloudBridgeDeploy. It need not exist yet: the next page gives you a script that creates it."
                {...field('role_arn')}
                error={form.errorFor('role_arn') ?? (form.submitted ? accountMismatch : null)}
              />
              {isEcs && (
                <TextField
                  label="ECS cluster (optional)"
                  mono
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="default"
                  hint="Express Mode creates the default cluster with the first service."
                  {...field('ecs_cluster')}
                />
              )}
            </>
          ) : isKubernetes ? (
            <>
              <TextField
                label="API server"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                className="form-grid__full"
                placeholder="https://my-cluster-dns-abc123.hcp.eastus.azmk8s.io:443"
                hint="The cluster's public API server address (server: in its kubeconfig)."
                {...field('api_server')}
              />
              <TextAreaField
                label="Cluster CA certificate"
                required
                mono
                spellCheck={false}
                className="form-grid__full"
                rows={5}
                placeholder="-----BEGIN CERTIFICATE-----"
                hint="Public, base64-decoded certificate-authority-data from the kubeconfig. Never paste a private key."
                {...field('ca_certificate')}
              />
              <TextField label="Namespace" required mono spellCheck={false} autoComplete="off" placeholder="apps" hint="CloudBridge works only in this namespace." {...field('namespace')} />
              <SelectField
                label="How CloudBridge signs in"
                required
                value={k8sAuth}
                onChange={(e) => setK8sAuth(e.target.value === 'oidc' ? 'oidc' : 'aks')}
                disabled={create.isPending}
              >
                <option value="aks">AKS with Microsoft Entra ID (app registration)</option>
                <option value="oidc">Cluster trusts CloudBridge’s OIDC token</option>
              </SelectField>
              {k8sAuth === 'aks' ? (
                <>
                  <TextField label="Tenant ID" required mono spellCheck={false} autoComplete="off" placeholder="00000000-0000-0000-0000-000000000000" {...field('tenant_id')} />
                  <TextField label="Application (client) ID" required mono spellCheck={false} autoComplete="off" placeholder="00000000-0000-0000-0000-000000000000" hint="Grant it Azure Kubernetes Service RBAC Writer on the namespace." {...field('client_id')} />
                </>
              ) : (
                <TextField label="Audience" required mono spellCheck={false} autoComplete="off" placeholder="cloudbridge" hint="The audience your cluster's JWT authenticator expects." {...field('audience')} />
              )}
            </>
          ) : isAzure ? (
            <>
              <TextField label="Tenant ID" required mono spellCheck={false} autoComplete="off" placeholder="00000000-0000-0000-0000-000000000000" hint="Directory (tenant) ID of your Microsoft Entra ID." {...field('tenant_id')} />
              <TextField label="Subscription ID" required mono spellCheck={false} autoComplete="off" placeholder="00000000-0000-0000-0000-000000000000" {...field('subscription_id')} />
              <TextField
                label="Application (client) ID"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="00000000-0000-0000-0000-000000000000"
                hint="The app registration CloudBridge signs in as. You add its federated credential in the next step."
                {...field('client_id')}
              />
              <TextField label="Resource group" required mono spellCheck={false} autoComplete="off" placeholder="cloudbridge-apps" hint="CloudBridge creates container apps only in this resource group." {...field('resource_group')} />
              <TextField
                label="Container Apps environment"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="cloudbridge-env"
                hint="An existing environment in the resource group, in the default region."
                {...field('environment_name')}
              />
              <TextField
                label="Container registry (optional)"
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="acmeregistry"
                hint="Azure Container Registry name, without .azurecr.io. Needed to build from repositories."
                {...field('registry_name')}
              />
            </>
          ) : (
            <>
              <TextField label="Project ID" required mono spellCheck={false} autoComplete="off" placeholder="my-project-123" {...field('project_id')} />
              <TextField
                label="Service account email"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="cloudbridge@my-project-123.iam.gserviceaccount.com"
                {...field('service_account_email')}
              />
              <TextField
                label="Workload identity provider"
                required
                mono
                spellCheck={false}
                autoComplete="off"
                className="form-grid__full"
                placeholder="projects/123456789/locations/global/workloadIdentityPools/cloudbridge/providers/cloudbridge"
                hint="Full resource name of the provider in your workload identity pool."
                {...field('workload_identity_provider')}
              />
            </>
          )}
        </div>
      </Panel>
      <Panel title="Permissions CloudBridge needs">
        <ProviderPermissions provider={provider} />
      </Panel>
      <Alert tone="info" title="No secret keys are needed">
        {NO_KEYS[provider]}
      </Alert>
      <div className="form-actions">
        <ButtonLink to="/providers">Cancel</ButtonLink>
        <Button type="submit" variant="primary" loading={create.isPending} loadingLabel="Saving…" disabled={form.submitted && (!form.isValid || Boolean(accountMismatch))} icon={<ShieldCheck size={16} aria-hidden="true" />}>
          Save and continue
        </Button>
      </div>
      <UnsavedChangesDialog blocker={blocker} />
    </form>
  );
}

export function ProviderSetupPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get('provider');
  const provider = PROVIDER_KINDS.find((k) => k === requested) ?? null;

  return (
    <div className="page">
      <PageHeader
        title="Connect a cloud provider"
        description="Give CloudBridge scoped access to deploy to one cloud account, project or subscription."
        breadcrumbs={[
          { label: 'Overview', to: '/' },
          { label: 'Cloud providers', to: '/providers' },
          { label: 'Connect' },
        ]}
      />
      <RequirePermission permission="providers:manage" resource="provider connection setup">
        <Panel title="1. Choose a provider">
          <fieldset className="fieldset">
            <legend className="visually-hidden">Provider</legend>
            <div className="radio-cards">
              {PROVIDER_KINDS.map((kind) => (
                <label className="radio-card" key={kind}>
                  <input
                    type="radio"
                    name="provider"
                    value={kind}
                    checked={provider === kind}
                    onChange={() => setParams({ provider: kind }, { replace: true })}
                  />
                  <span className="radio-card__text">
                    <strong>{PROVIDERS[kind].label}</strong>
                    <span className="field__hint">
                      {PROVIDER_HINTS[kind]}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </Panel>
        {provider ? (
          <SetupForm key={provider} provider={provider} />
        ) : (
          <p className="text-muted">
            <Cloud size={14} aria-hidden="true" className="icon-inline" /> Choose a provider to continue.
          </p>
        )}
      </RequirePermission>
    </div>
  );
}
