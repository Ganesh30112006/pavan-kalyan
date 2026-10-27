import type {
  ApplicationType,
  CreateApplicationRequest,
  Exposure,
  GitRefType,
  ProviderKind,
  RuntimeType,
} from '../../../types/api';
import type { CheckedField, SpecToCheck } from '../../../utils/capabilities';
import { fromEnvRows, fromSecretRows, type EnvRow, type SecretRow } from '../../../utils/environment';
import { validateRepositoryUrl } from '../../../utils/url';
import {
  applicationName,
  buildContext,
  compose,
  containerImage,
  gitBranch,
  gitCommit,
  healthCheckPath,
  integerInRange,
  maxLength,
  optionalIntegerInRange,
  relativePath,
  required,
  type Validator,
} from '../../../utils/validation';

/** Form values are strings while editing; toRequest() converts them to the typed request. */
export const INITIAL_VALUES = {
  // 1. Details
  name: '',
  description: '',
  application_type: 'web_service',
  // 2. Source
  source_type: 'repository',
  image: '',
  repository_url: '',
  ref_type: 'branch',
  git_ref: 'main',
  dockerfile_path: 'Dockerfile',
  build_context: '.',
  // 3. Runtime
  runtime: 'node',
  port: '8080',
  health_check_path: '/healthz',
  cpu: '1',
  memory: '2Gi',
  // 4. Scaling and exposure
  min_instances: '1',
  max_instances: '3',
  concurrency: '',
  exposure: 'public',
  // 6. Target
  provider: '',
  connection_id: '',
  region: '',
};

export type WizardValues = typeof INITIAL_VALUES;
export type WizardField = keyof WizardValues;

export interface WizardStep {
  title: string;
  fields: WizardField[];
}

export const STEPS: readonly WizardStep[] = [
  { title: 'Details', fields: ['name', 'description', 'application_type'] },
  { title: 'Source', fields: ['source_type', 'image', 'repository_url', 'ref_type', 'git_ref', 'dockerfile_path', 'build_context'] },
  { title: 'Runtime', fields: ['runtime', 'port', 'health_check_path', 'cpu', 'memory'] },
  { title: 'Scaling', fields: ['min_instances', 'max_instances', 'concurrency', 'exposure'] },
  { title: 'Environment', fields: [] },
  { title: 'Target', fields: ['provider', 'connection_id', 'region'] },
  { title: 'Review', fields: [] },
];

export const ENVIRONMENT_STEP = 4;
export const TARGET_STEP = 5;
export const REVIEW_STEP = 6;

/** The wizard step that owns a field checked against provider capabilities. */
export const FIELD_STEP: Record<CheckedField, number> = {
  application_type: 0,
  cpu: 2,
  memory: 2,
  min_instances: 3,
  max_instances: 3,
  concurrency: 3,
  exposure: 3,
  secret_refs: ENVIRONMENT_STEP,
  region: TARGET_STEP,
  source_type: 1,
};

/** Server field paths (422 `fields`) → wizard fields. Unmatched paths are listed in the error summary. */
export const SERVER_FIELDS: Record<string, WizardField> = {
  'source.type': 'source_type',
  'source.image': 'image',
  'source.repository_url': 'repository_url',
  'source.ref_type': 'ref_type',
  'source.git_ref': 'git_ref',
  'source.dockerfile_path': 'dockerfile_path',
  'source.build_context': 'build_context',
};

export function buildValidators(values: WizardValues): Partial<Record<WizardField, Validator>> {
  const v: Partial<Record<WizardField, Validator>> = {
    name: applicationName,
    description: maxLength('Description', 280),
    application_type: required('Application type'),
    runtime: required('Runtime'),
    port: integerInRange('Container port', 1, 65535),
    health_check_path: healthCheckPath,
    cpu: required('CPU'),
    memory: required('Memory'),
    min_instances: integerInRange('Minimum instances', 0, 1000),
    max_instances: integerInRange('Maximum instances', 1, 1000),
    concurrency: optionalIntegerInRange('Request concurrency', 1, 1000),
    exposure: required('Exposure'),
    provider: required('Cloud provider'),
    connection_id: required('Cloud account'),
    region: required('Region'),
  };
  if (values.source_type === 'image') {
    v.image = containerImage;
  } else {
    v.repository_url = compose(required('Repository URL'), (value) => validateRepositoryUrl(value.trim()));
    v.git_ref = values.ref_type === 'commit' ? gitCommit : gitBranch;
    v.dockerfile_path = relativePath('Dockerfile path');
    v.build_context = buildContext;
  }
  return v;
}

/** Errors that involve more than one field. */
export function crossFieldErrors(values: WizardValues): Partial<Record<WizardField, string>> {
  const min = Number(values.min_instances);
  const max = Number(values.max_instances);
  if (Number.isInteger(min) && Number.isInteger(max) && values.min_instances !== '' && min > max) {
    return { max_instances: 'Maximum instances must be at least the minimum.' };
  }
  return {};
}

export function toSpecToCheck(values: WizardValues, secretCount: number): SpecToCheck {
  return {
    application_type: values.application_type as ApplicationType,
    region: values.region,
    cpu: values.cpu,
    memory: values.memory,
    min_instances: Number(values.min_instances),
    max_instances: Number(values.max_instances),
    concurrency: values.concurrency.trim() === '' ? null : Number(values.concurrency),
    exposure: values.exposure as Exposure,
    secretRefCount: secretCount,
    sourceType: values.source_type === 'image' ? 'image' : 'repository',
  };
}

export function toRequest(values: WizardValues, env: EnvRow[], secrets: SecretRow[]): CreateApplicationRequest {
  const isImage = values.source_type === 'image';
  return {
    name: values.name,
    description: values.description.trim() || null,
    application_type: values.application_type as ApplicationType,
    runtime: values.runtime as RuntimeType,
    connection_id: values.connection_id,
    region: values.region,
    source: {
      type: isImage ? 'image' : 'repository',
      image: isImage ? values.image.trim() : null,
      repository_url: isImage ? null : values.repository_url.trim(),
      ref_type: isImage ? null : (values.ref_type as GitRefType),
      git_ref: isImage ? null : values.git_ref.trim(),
      dockerfile_path: isImage ? null : values.dockerfile_path.trim(),
      build_context: isImage ? null : values.build_context.trim(),
    },
    port: Number(values.port),
    cpu: values.cpu,
    memory: values.memory,
    min_instances: Number(values.min_instances),
    max_instances: Number(values.max_instances),
    concurrency: values.concurrency.trim() === '' ? null : Number(values.concurrency),
    exposure: values.exposure as Exposure,
    health_check_path: values.health_check_path.trim() || null,
    environment: fromEnvRows(env),
    secret_refs: fromSecretRows(secrets),
  };
}

export function asProvider(value: string): ProviderKind | null {
  return value === 'aws_app_runner' || value === 'gcp_cloud_run' || value === 'azure_container_apps' ? value : null;
}
