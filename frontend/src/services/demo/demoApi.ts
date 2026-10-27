import type {
  Application,
  ApplicationSummary,
  ConnectionSetup,
  ConnectionValidation,
  Deployment,
  DeploymentEvent,
  DeploymentStage,
  DeploymentStatus,
  DeploymentSummary,
  HealthStatus,
  LogEntry,
  Paginated,
  ProviderConnection,
  ProviderKind,
  SortParam,
  SpecIssue,
  StageName,
} from '../../types/api';
import { capabilityFindings } from '../../utils/capabilities';
import { randomId } from '../../utils/id';
import { createDemoPlatform } from './demoPlatform';
import { ApiError } from '../api/errors';
import type { CloudBridgeApi } from '../api/contract';
import { DEMO_CAPABILITIES, simulateEffective } from './capabilities';
import { createSeed, type DemoState } from './seed';

const isAws = (provider: ProviderKind) => provider === 'aws_app_runner' || provider === 'aws_ecs_express';

/** What a demo account must trust: shaped like the API's, with addresses that cannot resolve. */
function demoSetup(provider: ProviderKind): ConnectionSetup {
  const federated = provider === 'azure_container_apps' || provider === 'kubernetes';
  return {
    external_id: null,
    trusted_principal:
      isAws(provider) || federated
        ? 'arn:aws:iam::000000000000:role/cloudbridge-demo-worker'
        : 'principal://demo.invalid/cloudbridge-worker',
    federation_issuer: federated ? 'https://demo.invalid.tokens.sts.global.api.aws' : null,
    federation_audience: federated ? 'api://AzureADTokenExchange' : null,
    aws: isAws(provider)
      ? {
          ecr_repository_prefix: 'cloudbridge',
          apprunner_access_role: 'CloudBridgeAppRunnerECRAccess',
          apprunner_instance_role: 'CloudBridgeAppRunnerInstance',
          codebuild_role: 'CloudBridgeCodeBuild',
          ecs_execution_role: 'CloudBridgeECSExecution',
          ecs_infrastructure_role: 'CloudBridgeECSInfrastructure',
        }
      : null,
  };
}

/**
 * Development-only simulated backend. It is selected once at start-up when
 * VITE_DEMO_MODE=true in a non-production build, and is never used as a
 * fallback when the real API fails. Nothing here talks to a cloud provider.
 */

const STAGES: readonly StageName[] = ['queued', 'validation', 'build', 'publishing', 'provisioning', 'verification'];
const STAGE_MS = 3500;
const VALIDATION_STEP_MS = 2500;

/** The deployment status reported while a given stage is running. */
const STAGE_STATUS: Record<StageName, DeploymentStatus> = {
  queued: 'queued',
  validation: 'validating',
  build: 'building',
  publishing: 'publishing',
  provisioning: 'deploying',
  verification: 'verifying',
};

const HEALTH_ORDER: Record<HealthStatus, number> = { unhealthy: 0, degraded: 1, unknown: 2, healthy: 3 };

interface DeploymentSimulation {
  startedMs: number;
  failAt: StageName | null;
  cancelRequestedMs: number | null;
}

interface ValidationSimulation {
  startedMs: number;
  outcome: 'connected' | 'insufficient_permissions';
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function delay(signal?: AbortSignal): Promise<void> {
  const ms = 250 + Math.random() * 350;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new ApiError({ kind: 'aborted' }));
      },
      { once: true },
    );
  });
}

function notFound(): ApiError {
  return new ApiError({ kind: 'http', status: 404, code: 'not_found', requestId: `demo-${randomId().slice(0, 8)}` });
}

function paginate<T>(items: T[], page: number, pageSize: number): Paginated<T> {
  const start = (page - 1) * pageSize;
  return { items: items.slice(start, start + pageSize), total: items.length, page, page_size: pageSize };
}

function sortBy<T, K extends string>(items: T[], sort: SortParam<K> | undefined, compare: Record<K, (a: T, b: T) => number>): T[] {
  if (!sort) return items;
  const fn = compare[sort.key];
  const sorted = [...items].sort(fn);
  return sort.direction === 'desc' ? sorted.reverse() : sorted;
}

const iso = (ms: number) => new Date(ms).toISOString();

function summary(d: Deployment): DeploymentSummary {
  return {
    id: d.id,
    application_id: d.application_id,
    application_name: d.application_name,
    provider: d.provider,
    region: d.region,
    status: d.status,
    kind: d.kind,
    version: d.version,
    image_digest: d.image_digest,
    source_ref: d.source_ref,
    initiated_by: d.initiated_by,
    created_at: d.created_at,
    finished_at: d.finished_at,
    trigger: d.trigger,
  };
}

function appSummary(a: Application): ApplicationSummary {
  return {
    id: a.id,
    name: a.name,
    description: a.description,
    application_type: a.application_type,
    runtime: a.runtime,
    provider: a.provider,
    connection_id: a.connection_id,
    region: a.region,
    status: a.status,
    health: a.health,
    current_version: a.current_version,
    latest_deployment: a.latest_deployment,
    updated_at: a.updated_at,
  };
}

export function createDemoApi(): CloudBridgeApi {
  const state: DemoState = createSeed();
  let signedIn = false;
  const deploymentSims = new Map<string, DeploymentSimulation>();
  const validationSims = new Map<string, ValidationSimulation>();
  const idempotency = new Map<string, unknown>();
  const listeners = new Map<string, Set<() => void>>();

  function requireSession() {
    if (!signedIn) {
      listeners.get('session-expired')?.forEach((l) => l());
      throw new ApiError({ kind: 'http', status: 401, code: 'unauthenticated' });
    }
  }

  function audit(action: string, resourceType: string, resourceId: string | null, resourceName: string | null) {
    state.audit.unshift({
      id: `demo-audit-${randomId().slice(0, 8)}`,
      occurred_at: new Date().toISOString(),
      actor: { id: state.session.user.id, display_name: state.session.user.display_name, email: state.session.user.email },
      action,
      resource_type: resourceType,
      resource_id: resourceId,
      resource_name: resourceName,
      outcome: 'success',
      request_id: `demo-${randomId().slice(0, 8)}`,
    });
  }

  /** The deployment whose revision currently serves traffic for an application. */
  function servingDeploymentId(applicationId: string): string | null {
    const serving = state.deployments
      .filter((d) => d.application_id === applicationId && d.status === 'succeeded')
      .sort((a, b) => (b.finished_at ?? '').localeCompare(a.finished_at ?? ''))[0];
    return serving?.id ?? null;
  }

  /** What the simulated backend permits; the UI only offers these. */
  function refreshActions(dep: Deployment): void {
    const perms = state.session.permissions;
    dep.allowed_actions = {
      // Only an earlier successful revision that is not already serving traffic can be restored.
      rollback:
        perms.includes('deployments:rollback') &&
        dep.status === 'succeeded' &&
        dep.image_digest !== null &&
        servingDeploymentId(dep.application_id) !== dep.id,
      cancel: perms.includes('deployments:cancel') && deploymentSims.has(dep.id) && dep.kind === 'deploy',
      approve: false,
    };
  }

  /** Recomputes a simulated deployment from elapsed time. */
  function advanceDeployment(dep: Deployment): void {
    const sim = deploymentSims.get(dep.id);
    if (!sim) {
      refreshActions(dep);
      return;
    }
    const now = Date.now();
    const elapsed = now - sim.startedMs;
    const cancelled = sim.cancelRequestedMs !== null && now - sim.cancelRequestedMs > 2000;
    const stages: DeploymentStage[] = [];
    let terminal: 'failed' | 'cancelled' | null = null;
    let current: StageName | null = null;

    STAGES.forEach((name, index) => {
      const startMs = sim.startedMs + index * STAGE_MS;
      const endMs = startMs + STAGE_MS;
      if (terminal) {
        stages.push({ name, status: terminal === 'cancelled' ? 'cancelled' : 'skipped', started_at: null, finished_at: null });
        return;
      }
      if (elapsed < index * STAGE_MS) {
        if (cancelled) {
          terminal = 'cancelled';
          stages.push({ name, status: 'cancelled', started_at: null, finished_at: null });
        } else {
          stages.push({ name, status: 'pending', started_at: null, finished_at: null });
        }
        return;
      }
      if (now < endMs) {
        if (cancelled) {
          terminal = 'cancelled';
          stages.push({ name, status: 'cancelled', started_at: iso(startMs), finished_at: iso(now) });
        } else {
          current = name;
          stages.push({ name, status: 'active', started_at: iso(startMs), finished_at: null });
        }
        return;
      }
      if (sim.failAt === name) {
        terminal = 'failed';
        stages.push({ name, status: 'failed', started_at: iso(startMs), finished_at: iso(endMs) });
        return;
      }
      stages.push({ name, status: 'succeeded', started_at: iso(startMs), finished_at: iso(endMs) });
    });

    dep.stages = stages;
    const rollback = dep.kind === 'rollback';
    const allDone = stages.every((s) => s.status === 'succeeded');
    if (terminal === 'failed') {
      dep.status = rollback ? 'failed_rollback' : 'failed';
      dep.failure = {
        code: rollback ? 'rollback_failed' : 'build_failed',
        message: 'Simulated failure: the demo build step failed. No cloud resources were changed.',
      };
    } else if (terminal === 'cancelled') {
      dep.status = 'cancelled';
    } else if (allDone) {
      dep.status = rollback ? 'rolled_back' : 'succeeded';
    } else if (rollback) {
      dep.status = 'rolling_back';
    } else {
      dep.status = STAGE_STATUS[current ?? 'queued'];
    }

    const finished = !stages.some((s) => s.status === 'active' || s.status === 'pending') || terminal !== null;
    if (finished && !dep.finished_at) {
      dep.finished_at = new Date().toISOString();
      deploymentSims.delete(dep.id);
      const app = state.applications.find((a) => a.id === dep.application_id);
      if (app) {
        const restored = dep.status === 'succeeded' || dep.status === 'rolled_back';
        if (restored) {
          app.status = 'running';
          app.health = 'healthy';
          app.current_version = dep.version;
          app.image_digest = dep.image_digest;
          app.service_url = `https://${app.name}.demo.invalid`;
          dep.service_url = app.service_url;
          const config = state.configurations[app.id];
          if (config) Object.assign(config, simulateEffective(config, app.provider, dep.finished_at));
        } else if (dep.status !== 'cancelled') {
          app.status = app.current_version ? 'running' : 'failed';
          if (!app.current_version) app.health = 'unhealthy';
        } else if (!app.current_version) {
          app.status = 'draft';
        } else {
          app.status = 'running';
        }
        app.latest_deployment = { id: dep.id, status: dep.status, created_at: dep.created_at };
        app.updated_at = dep.finished_at;
      }
    }
    if (!dep.started_at && elapsed >= STAGE_MS) dep.started_at = iso(sim.startedMs + STAGE_MS);
    refreshActions(dep);
  }

  function advanceConnection(conn: ProviderConnection): void {
    const sim = validationSims.get(conn.id);
    if (!sim) return;
    const elapsed = Date.now() - sim.startedMs;
    const steps: Array<NonNullable<ConnectionValidation['stage']>> = [
      'connecting',
      'validating_permissions',
      'checking_readiness',
    ];
    const stepIndex = Math.floor(elapsed / VALIDATION_STEP_MS);
    const checkNames =
      conn.provider === 'aws_app_runner'
        ? ['Assume role', 'App Runner permissions', 'ECR read access']
        : conn.provider === 'azure_container_apps'
          ? ['Exchange federated token', 'Resource group access', 'Container Apps environment']
          : ['Exchange workload identity token', 'Cloud Run Admin', 'Artifact Registry read access'];

    if (stepIndex < steps.length) {
      conn.status = 'validating';
      conn.validation = {
        stage: steps[stepIndex] ?? null,
        started_at: iso(sim.startedMs),
        finished_at: null,
        checks: checkNames.map((name, i) => ({
          name,
          status: i < stepIndex ? 'passed' : 'pending',
          detail: null,
        })),
        error_code: null,
      };
      return;
    }
    validationSims.delete(conn.id);
    const failed = sim.outcome === 'insufficient_permissions';
    conn.status = sim.outcome;
    conn.last_validated_at = new Date().toISOString();
    conn.validation = {
      stage: null,
      started_at: iso(sim.startedMs),
      finished_at: conn.last_validated_at,
      checks: checkNames.map((name, i) => ({
        name,
        status: failed && i === 1 ? 'failed' : failed && i === 2 ? 'skipped' : 'passed',
        detail: failed && i === 1 ? 'Simulated: the role is missing a required permission.' : null,
      })),
      error_code: failed ? 'insufficient_permissions' : null,
    };
  }

  function findApp(id: string): Application {
    const app = state.applications.find((a) => a.id === id);
    if (!app) throw notFound();
    return app;
  }

  function findDeployment(id: string): Deployment {
    const dep = state.deployments.find((d) => d.id === id);
    if (!dep) throw notFound();
    advanceDeployment(dep);
    return dep;
  }

  function findConnection(id: string): ProviderConnection {
    const conn = state.connections.find((c) => c.id === id);
    if (!conn) throw notFound();
    advanceConnection(conn);
    return conn;
  }

  function advanceAppDeployments(app: Application) {
    if (!app.latest_deployment) return;
    const dep = state.deployments.find((d) => d.id === app.latest_deployment?.id);
    if (dep) advanceDeployment(dep);
  }

  function startDeployment(app: Application, kind: Deployment['kind'], target: Deployment | null): Deployment {
    const now = Date.now();
    const nextVersion = target?.version ?? `${app.current_version ?? '0.0'}-demo.${state.deployments.length + 1}`;
    const dep: Deployment = {
      id: `demo-dep-${randomId().slice(0, 8)}`,
      application_id: app.id,
      application_name: app.name,
      provider: app.provider,
      region: app.region,
      status: kind === 'rollback' ? 'rolling_back' : 'queued',
      kind,
      version: nextVersion,
      image_digest: target?.image_digest ?? `sha256:${randomId().replace(/-/g, '').repeat(3).slice(0, 64)}`,
      source_ref: app.source.type === 'image' ? (app.source.image?.split('/').pop() ?? null) : `${app.source.git_ref ?? 'main'}`,
      initiated_by: state.session.user.display_name,
      created_at: iso(now),
      started_at: null,
      finished_at: null,
      rollback_of: target?.id ?? null,
      stages: STAGES.map((name) => ({ name, status: name === 'queued' ? 'active' : 'pending', started_at: null, finished_at: null })),
      failure: null,
      service_url: null,
      progress_percent: null,
      allowed_actions: { rollback: false, cancel: false, approve: false },
      trigger: 'manual',
      approved_by: null,
      approved_at: null,
    };
    state.deployments.unshift(dep);
    deploymentSims.set(dep.id, {
      startedMs: now,
      failAt: app.name.includes('fail') ? 'build' : null,
      cancelRequestedMs: null,
    });
    app.status = 'deploying';
    app.latest_deployment = { id: dep.id, status: dep.status, created_at: dep.created_at };
    refreshActions(dep);
    audit(kind === 'rollback' ? 'deployment.rollback_started' : 'deployment.created', 'deployment', dep.id, app.name);
    return dep;
  }

  function once<T>(key: string, produce: () => T): T {
    if (idempotency.has(key)) return idempotency.get(key) as T;
    const result = produce();
    idempotency.set(key, result);
    return result;
  }

  function simulatedLogs(subject: string, stages: DeploymentStage[] | null): LogEntry[] {
    if (stages) {
      return stages
        .filter((s) => s.started_at)
        .flatMap((s) => [
          { timestamp: s.started_at!, stream: 'system' as const, message: `[demo] ${s.name}: started (simulated, no cloud call made)` },
          ...(s.finished_at
            ? [
                {
                  timestamp: s.finished_at,
                  stream: s.status === 'failed' ? ('stderr' as const) : ('stdout' as const),
                  message: `[demo] ${s.name}: ${s.status}`,
                },
              ]
            : []),
        ]);
    }
    const now = Date.now();
    return [0, 1, 2, 3, 4].map((i) => ({
      timestamp: iso(now - (5 - i) * 45_000),
      stream: i === 3 ? ('stderr' as const) : ('stdout' as const),
      message:
        i === 3
          ? `[demo] ${subject}: simulated warning — slow upstream response (1200 ms)`
          : `[demo] ${subject}: simulated request GET /healthz 200`,
    }));
  }

  return {
    mode: 'demo',
    on(event, listener) {
      let set = listeners.get(event);
      if (!set) {
        set = new Set();
        listeners.set(event, set);
      }
      set.add(listener);
      return () => set.delete(listener);
    },

    auth: {
      async getSession(signal) {
        await delay(signal);
        if (!signedIn) throw new ApiError({ kind: 'http', status: 401, code: 'unauthenticated' });
        return clone(state.session);
      },
      async login(input) {
        await delay();
        if (!input.email.trim() || !input.password) {
          throw new ApiError({ kind: 'http', status: 401, code: 'invalid_credentials', method: 'POST' });
        }
        signedIn = true;
        return clone(state.session);
      },
      async logout() {
        await delay();
        signedIn = false;
      },
    },

    applications: {
      async list(params, signal) {
        await delay(signal);
        requireSession();
        state.applications.forEach(advanceAppDeployments);
        const q = params.search?.trim().toLowerCase();
        const filtered = state.applications.filter(
          (a) =>
            (!q || a.name.includes(q) || (a.description ?? '').toLowerCase().includes(q)) &&
            (!params.status || a.status === params.status) &&
            (!params.health?.length || params.health.includes(a.health)) &&
            (!params.provider || a.provider === params.provider),
        );
        const sorted = sortBy(filtered, params.sort, {
          name: (a, b) => a.name.localeCompare(b.name),
          updated_at: (a, b) => a.updated_at.localeCompare(b.updated_at),
          health: (a, b) => HEALTH_ORDER[a.health] - HEALTH_ORDER[b.health],
        });
        return clone(paginate(sorted.map(appSummary), params.page, params.page_size));
      },
      async get(id, signal) {
        await delay(signal);
        requireSession();
        const app = findApp(id);
        advanceAppDeployments(app);
        return clone(app);
      },
      async create(input, { idempotencyKey }) {
        await delay();
        requireSession();
        return clone(
          once(idempotencyKey, () => {
            if (state.applications.some((a) => a.name === input.name)) {
              throw new ApiError({
                kind: 'http',
                status: 409,
                code: 'duplicate_name',
                serverMessage: 'An application with this name already exists.',
                fieldErrors: [{ field: 'name', message: 'This name is already in use.' }],
                method: 'POST',
              });
            }
            const conn = state.connections.find((c) => c.id === input.connection_id);
            if (!conn) {
              throw new ApiError({
                kind: 'http',
                status: 422,
                fieldErrors: [{ field: 'connection_id', message: 'Select a provider connection.' }],
                method: 'POST',
              });
            }
            const blocking = capabilityFindings(
              { ...input, secretRefCount: input.secret_refs.length },
              DEMO_CAPABILITIES[conn.provider],
            ).filter((f) => f.severity === 'error');
            if (blocking.length > 0) {
              throw new ApiError({
                kind: 'http',
                status: 422,
                fieldErrors: blocking.map((f) => ({ field: f.field, message: f.message })),
                method: 'POST',
              });
            }
            const now = new Date().toISOString();
            const app: Application = {
              id: `demo-app-${randomId().slice(0, 8)}`,
              name: input.name,
              description: input.description,
              application_type: input.application_type,
              runtime: input.runtime,
              provider: conn.provider,
              connection_id: conn.id,
              region: input.region,
              status: 'draft',
              health: 'unknown',
              current_version: null,
              latest_deployment: null,
              updated_at: now,
              source: input.source,
              image_digest: null,
              service_url: null,
              created_at: now,
              created_by: state.session.user.display_name,
              environment: 'production',
              promote_to_id: null,
              preview_of_id: null,
              preview_number: null,
              paused: false,
              requires_approval: false,
              auto_deploy: false,
            };
            state.applications.unshift(app);
            state.configurations[app.id] = {
              application_id: app.id,
              version: 1,
              port: input.port,
              cpu: input.cpu,
              memory: input.memory,
              min_instances: input.min_instances,
              max_instances: input.max_instances,
              concurrency: input.concurrency,
              exposure: input.exposure,
              health_check_path: input.health_check_path,
              environment: input.environment,
              secret_refs: input.secret_refs,
              updated_at: now,
              effective: null,
              adjustments: [],
            };
            audit('application.created', 'application', app.id, app.name);
            return app;
          }),
        );
      },
      async remove(id) {
        await delay();
        requireSession();
        const app = findApp(id);
        state.applications = state.applications.filter((a) => a.id !== id);
        audit('application.deleted', 'application', id, app.name);
        return 'deleted';
      },
      async getConfiguration(id, signal) {
        await delay(signal);
        requireSession();
        findApp(id);
        const config = state.configurations[id];
        if (!config) throw notFound();
        return clone(config);
      },
      async updateConfiguration(id, input) {
        await delay();
        requireSession();
        const app = findApp(id);
        const current = state.configurations[id];
        if (!current) throw notFound();
        if (current.version !== input.version) {
          throw new ApiError({
            kind: 'http',
            status: 409,
            code: 'version_conflict',
            serverMessage: 'The configuration was changed by someone else. Reload to see the latest version.',
            method: 'PUT',
          });
        }
        const next = { ...current, ...input, version: current.version + 1, updated_at: new Date().toISOString() };
        state.configurations[id] = next;
        audit('application.configuration_updated', 'application', id, app.name);
        return clone(next);
      },
      async validate(id) {
        await delay();
        requireSession();
        const app = findApp(id);
        const config = state.configurations[id];
        if (!config) throw notFound();
        const findings = capabilityFindings(
          { ...config, application_type: app.application_type, region: app.region, secretRefCount: config.secret_refs.length },
          DEMO_CAPABILITIES[app.provider],
        );
        const issues: SpecIssue[] = findings
          .filter((f) => f.severity !== 'adjusted')
          .map((f) => ({ field: f.field, severity: f.severity === 'error' ? 'error' : 'warning', message: `Simulated: ${f.message}` }));
        const { adjustments } = simulateEffective(config, app.provider, new Date().toISOString());
        audit('application.spec_validated', 'application', id, app.name);
        return { valid: !issues.some((i) => i.severity === 'error'), issues, adjustments };
      },
      async getLogs(id, signal) {
        await delay(signal);
        requireSession();
        const app = findApp(id);
        if (!app.current_version) return { entries: [], truncated: false };
        return { entries: simulatedLogs(app.name, null), truncated: true };
      },
    },

    deployments: {
      async list(params, signal) {
        await delay(signal);
        requireSession();
        state.deployments.forEach(advanceDeployment);
        const q = params.search?.trim().toLowerCase();
        const filtered = [...state.deployments]
          .sort((a, b) => b.created_at.localeCompare(a.created_at))
          .filter(
            (d) =>
              (!params.application_id || d.application_id === params.application_id) &&
              (!params.status?.length || params.status.includes(d.status)) &&
              (!params.provider || d.provider === params.provider) &&
              (!q || d.application_name.includes(q) || d.id.toLowerCase().includes(q) || (d.version ?? '').includes(q)),
          );
        const sorted = sortBy(filtered, params.sort, {
          created_at: (a, b) => a.created_at.localeCompare(b.created_at),
          application_name: (a, b) => a.application_name.localeCompare(b.application_name),
          status: (a, b) => a.status.localeCompare(b.status),
        });
        return clone(paginate(sorted.map(summary), params.page, params.page_size));
      },
      async listForApplication(applicationId, params, signal) {
        await delay(signal);
        requireSession();
        findApp(applicationId);
        const items = state.deployments
          .filter((d) => d.application_id === applicationId)
          .map((d) => {
            advanceDeployment(d);
            return d;
          })
          .sort((a, b) => b.created_at.localeCompare(a.created_at));
        return clone(paginate(items.map(summary), params.page, params.page_size));
      },
      async get(id, signal) {
        await delay(signal);
        requireSession();
        return clone(findDeployment(id));
      },
      async listEvents(id, signal) {
        await delay(signal);
        requireSession();
        const dep = findDeployment(id);
        const events: DeploymentEvent[] = [];
        for (const stage of dep.stages) {
          if (stage.started_at) {
            events.push({
              id: `${dep.id}-${stage.name}-start`,
              timestamp: stage.started_at,
              level: 'info',
              stage: stage.name,
              message: `Simulated: ${stage.name} started.`,
            });
          }
          if (stage.finished_at) {
            const failed = stage.status === 'failed';
            events.push({
              id: `${dep.id}-${stage.name}-end`,
              timestamp: stage.finished_at,
              level: failed ? 'error' : stage.status === 'cancelled' ? 'warning' : 'info',
              stage: stage.name,
              message: `Simulated: ${stage.name} ${stage.status}.`,
            });
          }
        }
        return clone(events);
      },
      async getLogs(id, signal) {
        await delay(signal);
        requireSession();
        const dep = findDeployment(id);
        return { entries: simulatedLogs(dep.application_name, dep.stages), truncated: false };
      },
      async create(applicationId, { idempotencyKey }) {
        await delay();
        requireSession();
        const app = findApp(applicationId);
        return clone(
          once(idempotencyKey, () => {
            if (state.deployments.some((d) => d.application_id === app.id && deploymentSims.has(d.id))) {
              throw new ApiError({
                kind: 'http',
                status: 409,
                code: 'deployment_in_progress',
                serverMessage: 'A deployment for this application is already in progress.',
                method: 'POST',
              });
            }
            return startDeployment(app, 'deploy', null);
          }),
        );
      },
      async cancel(id) {
        await delay();
        requireSession();
        const dep = findDeployment(id);
        const sim = deploymentSims.get(id);
        if (!sim || !dep.allowed_actions.cancel) {
          throw new ApiError({
            kind: 'http',
            status: 409,
            code: 'not_cancellable',
            serverMessage: 'This deployment can no longer be cancelled.',
            method: 'POST',
          });
        }
        sim.cancelRequestedMs ??= Date.now();
        audit('deployment.cancel_requested', 'deployment', id, dep.application_name);
        return clone(dep);
      },
      async rollback(id, { idempotencyKey }) {
        await delay();
        requireSession();
        const target = findDeployment(id);
        const app = findApp(target.application_id);
        return clone(
          once(idempotencyKey, () => {
            if (!target.allowed_actions.rollback) {
              throw new ApiError({
                kind: 'http',
                status: 409,
                code: 'rollback_not_permitted',
                serverMessage: 'This deployment cannot be used as a rollback target.',
                method: 'POST',
              });
            }
            return startDeployment(app, 'rollback', target);
          }),
        );
      },
      async approve(id) {
        await delay();
        requireSession();
        const dep = findDeployment(id);
        if (dep.status !== 'awaiting_approval') {
          throw new ApiError({ kind: 'http', status: 409, code: 'not_awaiting_approval', serverMessage: 'This deployment is not waiting for approval.', method: 'POST' });
        }
        dep.status = 'queued';
        dep.approved_by = state.session.user.display_name;
        dep.approved_at = new Date().toISOString();
        audit('deployment.approved', 'deployment', id, dep.application_name);
        return clone(dep);
      },
      async reject(id) {
        await delay();
        requireSession();
        const dep = findDeployment(id);
        if (dep.status !== 'awaiting_approval') {
          throw new ApiError({ kind: 'http', status: 409, code: 'not_awaiting_approval', serverMessage: 'This deployment is not waiting for approval.', method: 'POST' });
        }
        dep.status = 'cancelled';
        dep.failure = { code: 'rejected', message: `Rejected by ${state.session.user.display_name}.` };
        dep.finished_at = new Date().toISOString();
        audit('deployment.rejected', 'deployment', id, dep.application_name);
        return clone(dep);
      },
    },

    providers: {
      async list(signal) {
        await delay(signal);
        requireSession();
        state.connections.forEach(advanceConnection);
        return clone(state.connections);
      },
      async getCapabilities(provider, signal) {
        await delay(signal);
        requireSession();
        return clone(DEMO_CAPABILITIES[provider]);
      },
      async getSetup(provider, signal) {
        await delay(signal);
        requireSession();
        return demoSetup(provider);
      },
      async get(id, signal) {
        await delay(signal);
        requireSession();
        return clone(findConnection(id));
      },
      async create(input, { idempotencyKey }) {
        await delay();
        requireSession();
        return clone(
          once(idempotencyKey, () => {
            const conn: ProviderConnection = {
              id: `demo-conn-${randomId().slice(0, 8)}`,
              name: input.name,
              provider: input.provider,
              status: 'pending_validation',
              default_region: input.default_region,
              config: {
                account_id: input.aws?.account_id ?? input.ecs?.account_id ?? null,
                role_arn: input.aws?.role_arn ?? input.ecs?.role_arn ?? null,
                project_id: input.gcp?.project_id ?? null,
                workload_identity_provider: input.gcp?.workload_identity_provider ?? null,
                service_account_email: input.gcp?.service_account_email ?? null,
                tenant_id: input.azure?.tenant_id ?? null,
                subscription_id: input.azure?.subscription_id ?? null,
                client_id: input.azure?.client_id ?? null,
                resource_group: input.azure?.resource_group ?? null,
                environment_name: input.azure?.environment_name ?? null,
                registry_name: input.azure?.registry_name?.toLowerCase() || null,
                ecs_cluster: input.ecs ? input.ecs.cluster || 'default' : null,
                api_server: input.kubernetes?.api_server ?? null,
                namespace: input.kubernetes?.namespace ?? null,
                kubernetes_auth: input.kubernetes?.auth ?? null,
                token_audience: input.kubernetes?.audience ?? null,
              },
              setup: {
                ...demoSetup(input.provider),
                external_id: isAws(input.provider) ? `demo-${randomId().slice(0, 12)}` : null,
                federation_audience:
                  input.provider === 'kubernetes' && input.kubernetes?.auth === 'oidc'
                    ? input.kubernetes.audience ?? null
                    : demoSetup(input.provider).federation_audience,
              },
              validation: null,
              created_at: new Date().toISOString(),
              last_validated_at: null,
            };
            state.connections.push(conn);
            audit('provider_connection.created', 'provider_connection', conn.id, conn.name);
            return conn;
          }),
        );
      },
      async validate(id) {
        await delay();
        requireSession();
        const conn = findConnection(id);
        if (!validationSims.has(id)) {
          const roleRef = conn.config.role_arn ?? conn.config.service_account_email ?? '';
          validationSims.set(id, {
            startedMs: Date.now(),
            outcome: /readonly/i.test(roleRef) ? 'insufficient_permissions' : 'connected',
          });
          advanceConnection(conn);
          audit('provider_connection.validation_started', 'provider_connection', id, conn.name);
        }
        return clone(conn);
      },
      async remove(id) {
        await delay();
        requireSession();
        const conn = findConnection(id);
        if (state.applications.some((a) => a.connection_id === id)) {
          throw new ApiError({
            kind: 'http',
            status: 409,
            code: 'connection_in_use',
            serverMessage: 'Applications still use this connection. Delete or move them first.',
            method: 'DELETE',
          });
        }
        state.connections = state.connections.filter((c) => c.id !== id);
        audit('provider_connection.deleted', 'provider_connection', id, conn.name);
        return 'deleted';
      },
    },

    audit: {
      async list(params, signal) {
        await delay(signal);
        requireSession();
        const q = params.search?.trim().toLowerCase();
        const actor = params.actor?.trim().toLowerCase();
        const filtered = [...state.audit].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)).filter(
          (e) =>
            (!q ||
              e.action.includes(q) ||
              (e.resource_name ?? '').toLowerCase().includes(q) ||
              (e.actor?.display_name ?? '').toLowerCase().includes(q)) &&
            (!actor ||
              (e.actor?.display_name ?? 'system').toLowerCase().includes(actor) ||
              (e.actor?.email ?? '').toLowerCase().includes(actor)) &&
            (!params.action || e.action === params.action) &&
            (!params.outcome || e.outcome === params.outcome) &&
            (!params.resource_type || e.resource_type === params.resource_type) &&
            (!params.from || e.occurred_at >= params.from) &&
            (!params.to || e.occurred_at <= params.to),
        );
        return clone(paginate(filtered, params.page, params.page_size));
      },
    },

    platform: createDemoPlatform({
      delay,
      requireSession,
      findApp,
      apps: () => state.applications,
      summary: appSummary,
      configuration: (id) => {
        const config = state.configurations[id];
        if (!config) throw notFound();
        return config;
      },
      startDeployment: (app) => startDeployment(app, 'deploy', null),
      once,
      audit,
    }),

    settings: {
      async updateProfile(input) {
        await delay();
        requireSession();
        state.session.user = { ...state.session.user, display_name: input.display_name, preferences: input.preferences };
        return clone(state.session.user);
      },
      async getOrganization(signal) {
        await delay(signal);
        requireSession();
        return { ...state.session.organization, created_at: state.organizationCreatedAt };
      },
      async updateOrganization(input) {
        await delay();
        requireSession();
        state.session.organization = { ...state.session.organization, name: input.name };
        audit('organization.updated', 'organization', state.session.organization.id, input.name);
        return { ...state.session.organization, created_at: state.organizationCreatedAt };
      },
      async listMembers(signal) {
        await delay(signal);
        requireSession();
        return clone(state.members);
      },
      async getNotificationPreferences(signal) {
        await delay(signal);
        requireSession();
        return clone(state.notifications);
      },
      async updateNotificationPreferences(input) {
        await delay();
        requireSession();
        state.notifications = clone(input);
        return clone(state.notifications);
      },
    },
  };
}
