import type { ApplicationType, Exposure, ProviderCapabilities } from '../types/api';
import { applicationTypeLabel } from './labels';
import { providerLabel } from './providers';

/**
 * - error:    the provider cannot run this; the user must change it.
 * - adjusted: the provider will apply a different value than requested.
 * - ignored:  the provider does not support the setting and will not apply it.
 */
export type FindingSeverity = 'error' | 'adjusted' | 'ignored';

export type CheckedField =
  | 'application_type'
  | 'region'
  | 'cpu'
  | 'memory'
  | 'min_instances'
  | 'max_instances'
  | 'concurrency'
  | 'exposure'
  | 'secret_refs'
  | 'source_type';

export interface CapabilityFinding {
  field: CheckedField;
  severity: FindingSeverity;
  message: string;
}

export interface SpecToCheck {
  application_type: ApplicationType;
  region: string;
  cpu: string;
  memory: string;
  min_instances: number;
  max_instances: number;
  concurrency: number | null;
  exposure: Exposure;
  secretRefCount: number;
  /** Omitted when the source is not known yet. */
  sourceType?: 'image' | 'repository';
}

/**
 * Compares a requested spec with what the provider reports it supports.
 * Only uses the capabilities the backend returned; the backend still has the final word.
 */
export function capabilityFindings(spec: SpecToCheck, caps: ProviderCapabilities): CapabilityFinding[] {
  const name = providerLabel(caps.provider);
  const findings: CapabilityFinding[] = [];
  const add = (field: CheckedField, severity: FindingSeverity, message: string) => findings.push({ field, severity, message });

  if (!caps.supported_application_types.includes(spec.application_type)) {
    add('application_type', 'error', `${name} does not run ${applicationTypeLabel[spec.application_type].toLowerCase()} applications.`);
  }
  if (spec.region && !caps.regions.includes(spec.region)) {
    add('region', 'error', `${name} is not available in ${spec.region}.`);
  }
  if (!caps.cpu_options.includes(spec.cpu)) {
    add('cpu', 'error', `${name} does not offer ${spec.cpu} vCPU. Supported: ${caps.cpu_options.join(', ')}.`);
  }
  if (!caps.memory_options.includes(spec.memory)) {
    add('memory', 'error', `${name} does not offer ${spec.memory} of memory. Supported: ${caps.memory_options.join(', ')}.`);
  }
  if (spec.min_instances < caps.min_instances_floor) {
    add(
      'min_instances',
      'adjusted',
      `${name} keeps at least ${caps.min_instances_floor} instance${caps.min_instances_floor === 1 ? '' : 's'} running; minimum instances will be ${caps.min_instances_floor}, not ${spec.min_instances}.`,
    );
  }
  if (spec.max_instances > caps.max_instances_limit) {
    add('max_instances', 'error', `${name} allows at most ${caps.max_instances_limit} instances.`);
  }
  if (spec.concurrency !== null) {
    if (!caps.supports_concurrency) {
      add('concurrency', 'ignored', `${name} does not support a request concurrency setting; it will be ignored.`);
    } else if (caps.max_concurrency !== null && spec.concurrency > caps.max_concurrency) {
      add('concurrency', 'error', `${name} allows at most ${caps.max_concurrency} concurrent requests per instance.`);
    }
  }
  if (spec.exposure === 'private' && !caps.supports_private_exposure) {
    add('exposure', 'error', `${name} cannot make services private through CloudBridge.`);
  }
  if (spec.secretRefCount > 0 && !caps.supports_secret_references) {
    add('secret_refs', 'error', `${name} does not support secret references through CloudBridge.`);
  }
  if (spec.sourceType === 'repository' && !caps.features.repository_builds) {
    add('source_type', 'error', `${name} cannot build from a repository through CloudBridge yet. Deploy a container image.`);
  }
  // Same rules as the backend: a worker has no web address, and nothing would wake one scaled to zero.
  if (spec.application_type === 'worker') {
    if (spec.exposure !== 'private') add('exposure', 'error', 'Background workers have no web address; set exposure to private.');
    if (spec.min_instances < 1) add('min_instances', 'error', 'Background workers need at least 1 instance.');
  }
  return findings;
}

export function hasBlockingFindings(findings: readonly CapabilityFinding[]): boolean {
  return findings.some((f) => f.severity === 'error');
}

export const findingLabel: Record<FindingSeverity, string> = {
  error: 'Not supported',
  adjusted: 'Will be adjusted',
  ignored: 'Will be ignored',
};
