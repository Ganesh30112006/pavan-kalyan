import type { WizardValues } from './wizardModel';

/**
 * The sample application anyone can deploy to try CloudBridge: examples/hello-web, a small web server
 * (Python standard library) with a Dockerfile and a /healthz health check. It is built from the
 * repository by the provider's build service, so it works on every provider that builds from a repository.
 */
export const DEFAULT_SAMPLE_REPOSITORY = 'https://github.com/Ganesh30112006/pavan-kalyan.git';

/** VITE_SAMPLE_APP_REPOSITORY points the sample at a fork or mirror (a public https URL; not a secret). */
export function sampleRepository(configured: string | undefined = import.meta.env.VITE_SAMPLE_APP_REPOSITORY): string {
  const raw = (configured ?? '').trim();
  if (!raw) return DEFAULT_SAMPLE_REPOSITORY;
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && !url.username && !url.password ? raw : DEFAULT_SAMPLE_REPOSITORY;
  } catch {
    return DEFAULT_SAMPLE_REPOSITORY;
  }
}

/** Every field the sample sets. Target fields (provider, connection, region) stay as the user chose them. */
export function sampleValues(repository: string = sampleRepository()): Partial<WizardValues> {
  return {
    name: 'hello-web',
    description: 'CloudBridge sample application (examples/hello-web)',
    application_type: 'web_service',
    source_type: 'repository',
    image: '',
    repository_url: repository,
    ref_type: 'branch',
    git_ref: 'main',
    dockerfile_path: 'examples/hello-web/Dockerfile',
    build_context: 'examples/hello-web',
    runtime: 'python',
    port: '8080',
    health_check_path: '/healthz',
    // The smallest size every provider offers keeps a trial run cheap.
    cpu: '0.25',
    memory: '512Mi',
    min_instances: '1',
    max_instances: '1',
    concurrency: '',
    exposure: 'public',
  };
}

/** Shown on the sample's page, so the user can see it change after editing and redeploying. */
export const SAMPLE_ENVIRONMENT = [{ name: 'APP_VERSION', value: '1.0.0' }] as const;
