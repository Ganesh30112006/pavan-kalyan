import type {
  AlertChannel,
  ApiToken,
  Application,
  ApplicationConfiguration,
  ApplicationSummary,
  Automation,
  CostEstimate,
  CustomDomain,
  Deployment,
  MetricRange,
  Metrics,
  ScheduleState,
} from '../../types/api';
import { ApiError } from '../api/errors';
import type { PlatformApi } from '../api/contract';
import { randomId } from '../../utils/id';

/** What the platform simulation needs from the rest of the demo backend. */
export interface DemoPlatformDeps {
  delay(signal?: AbortSignal): Promise<void>;
  requireSession(): void;
  findApp(id: string): Application;
  apps(): Application[];
  summary(app: Application): ApplicationSummary;
  configuration(id: string): ApplicationConfiguration;
  startDeployment(app: Application): Deployment;
  once<T>(key: string, create: () => T): T;
  audit(action: string, resourceType: string, resourceId: string | null, resourceName: string | null): void;
}

function conflict(code: string, message: string): ApiError {
  return new ApiError({ kind: 'http', status: 409, code, serverMessage: message, method: 'POST' });
}

const RANGE_MINUTES: Record<MetricRange, [number, number]> = { '1h': [60, 60], '6h': [360, 300], '24h': [1440, 900], '7d': [10080, 3600] };

/** Simulated metrics: a smooth daily curve, clearly marked as demo data by the UI's demo banner. */
function simulatedMetrics(range: MetricRange, seed: number): Metrics {
  const [minutes, step] = RANGE_MINUTES[range];
  const count = Math.min(60, Math.floor((minutes * 60) / step));
  const now = Date.now();
  const points = (scale: number, offset: number) =>
    Array.from({ length: count }, (_, i) => {
      const t = now - (count - 1 - i) * step * 1000;
      const wave = Math.sin((t / 3_600_000 + seed) / 3) * 0.4 + 0.6;
      return { timestamp: new Date(t).toISOString(), value: Math.round((offset + scale * wave) * 10) / 10 };
    });
  return {
    range,
    step_seconds: step,
    series: [
      { name: 'requests', unit: 'count', points: points(400, 20) },
      { name: 'errors_5xx', unit: 'count', points: points(3, 0) },
      { name: 'latency_ms', unit: 'ms', points: points(60, 40) },
      { name: 'instances', unit: 'count', points: points(1, 1) },
      { name: 'cpu_percent', unit: 'percent', points: points(35, 10) },
    ],
    unavailable_reason: null,
  };
}

export function createDemoPlatform(deps: DemoPlatformDeps): PlatformApi {
  const automation = new Map<string, Automation>();
  const schedules = new Map<string, ScheduleState>();
  const domains = new Map<string, CustomDomain[]>();
  const channels: AlertChannel[] = [];
  const tokens: ApiToken[] = [];

  function automationFor(app: Application): Automation {
    let value = automation.get(app.id);
    if (!value) {
      value = {
        environment: app.environment,
        promote_to_id: app.promote_to_id,
        auto_deploy: app.auto_deploy,
        auto_deploy_branch: null,
        previews_enabled: false,
        config_from_repo: false,
        requires_approval: app.requires_approval,
        webhook: { url: `https://demo.invalid/api/v1/hooks/github/${app.id}`, secret_configured: false },
      };
      automation.set(app.id, value);
    }
    return value;
  }

  function scheduleFor(id: string): ScheduleState {
    return schedules.get(id) ?? { schedule: null, paused: false, paused_by_schedule: false, asleep_now: false };
  }

  function cost(provider: CostEstimate['provider'], cpu: string, memory: string, min: number, max: number): CostEstimate {
    const gib = memory.endsWith('Gi') ? Number(memory.slice(0, -2)) : Number(memory.slice(0, -2)) / 1024;
    const hourly = Number(cpu) * 0.05 + gib * 0.006;
    if (provider === 'kubernetes') {
      return {
        provider,
        currency: 'USD',
        monthly_low: null,
        monthly_high: null,
        lines: [],
        assumptions: ['Simulated: Kubernetes runs on your cluster’s nodes, billed by your cloud.'],
      };
    }
    const low = Math.round(Math.max(min, provider === 'aws_app_runner' ? 1 : 0) * hourly * 730 * 100) / 100;
    const high = Math.round(Math.max(max, 1) * hourly * 730 * 100) / 100;
    return {
      provider,
      currency: 'USD',
      monthly_low: low,
      monthly_high: high,
      lines: [{ item: 'Instances', low, high }],
      assumptions: ['Simulated estimate from rough list prices.'],
    };
  }

  return {
    async getAutomation(id, signal) {
      await deps.delay(signal);
      deps.requireSession();
      return structuredClone(automationFor(deps.findApp(id)));
    },
    async updateAutomation(id, input) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      const next = { ...automationFor(app), ...input };
      if ((next.auto_deploy || next.previews_enabled) && !next.webhook.secret_configured) {
        next.webhook = { ...next.webhook, secret_configured: true };
      }
      automation.set(id, next);
      Object.assign(app, {
        environment: next.environment,
        promote_to_id: next.promote_to_id,
        requires_approval: next.requires_approval,
        auto_deploy: next.auto_deploy,
      });
      deps.audit('application.automation_updated', 'application', id, app.name);
      return structuredClone(next);
    },
    async rotateWebhookSecret(id) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      const current = automationFor(app);
      automation.set(id, { ...current, webhook: { ...current.webhook, secret_configured: true } });
      return { url: current.webhook.url, secret: `demo-${randomId()}` };
    },
    async applyConfigFile(id, input) {
      await deps.delay();
      deps.requireSession();
      const config = deps.configuration(id);
      if (config.version !== input.version) throw conflict('version_conflict', 'The configuration changed since it was loaded.');
      const port = /^port:\s*(\d+)/m.exec(input.content)?.[1];
      if (port) config.port = Number(port);
      config.version += 1;
      config.updated_at = new Date().toISOString();
      return structuredClone(config);
    },
    async listPreviews(id, signal) {
      await deps.delay(signal);
      deps.requireSession();
      return deps.apps().filter((a) => a.preview_of_id === id).map(deps.summary);
    },
    async promote(id, { idempotencyKey }) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      return structuredClone(
        deps.once(idempotencyKey, () => {
          if (!app.promote_to_id) throw conflict('no_promotion_target', 'Choose an application to promote to first.');
          if (!app.current_version) throw conflict('nothing_to_promote', `${app.name} has no successful deployment to promote yet.`);
          const dep = deps.startDeployment(deps.findApp(app.promote_to_id));
          dep.trigger = 'promotion';
          return dep;
        }),
      );
    },
    async getSchedule(id, signal) {
      await deps.delay(signal);
      deps.requireSession();
      return structuredClone(scheduleFor(id));
    },
    async setSchedule(id, schedule) {
      await deps.delay();
      deps.requireSession();
      const next = { ...scheduleFor(id), schedule };
      schedules.set(id, next);
      return structuredClone(next);
    },
    async deleteSchedule(id) {
      await deps.delay();
      deps.requireSession();
      const next = { ...scheduleFor(id), schedule: null, paused_by_schedule: false };
      schedules.set(id, next);
      return structuredClone(next);
    },
    async pause(id) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      app.paused = true;
      app.status = 'stopped';
      const next = { ...scheduleFor(id), paused: true, paused_by_schedule: false };
      schedules.set(id, next);
      deps.audit('application.paused', 'application', id, app.name);
      return structuredClone(next);
    },
    async resume(id) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      app.paused = false;
      app.status = 'running';
      const next = { ...scheduleFor(id), paused: false, paused_by_schedule: false };
      schedules.set(id, next);
      deps.audit('application.resumed', 'application', id, app.name);
      return structuredClone(next);
    },
    async getMetrics(id, range, signal) {
      await deps.delay(signal);
      deps.requireSession();
      const app = deps.findApp(id);
      if (!app.current_version) {
        return { range, step_seconds: RANGE_MINUTES[range][1], series: [], unavailable_reason: 'The application has not been deployed yet.' };
      }
      return simulatedMetrics(range, app.name.length);
    },
    async getCost(id, signal) {
      await deps.delay(signal);
      deps.requireSession();
      const app = deps.findApp(id);
      const config = deps.configuration(id);
      return cost(app.provider, config.cpu, config.memory, config.min_instances, config.max_instances);
    },
    async estimateCost(input, signal) {
      await deps.delay(signal);
      deps.requireSession();
      return cost(input.provider, input.cpu, input.memory, input.min_instances, input.max_instances);
    },
    async listDomains(id, signal) {
      await deps.delay(signal);
      deps.requireSession();
      return structuredClone(domains.get(id) ?? []);
    },
    async addDomain(id, domain) {
      await deps.delay();
      deps.requireSession();
      const app = deps.findApp(id);
      if (!app.current_version) throw conflict('not_deployed', 'Deploy the application before adding a domain.');
      const created: CustomDomain = {
        id: `demo-dom-${randomId().slice(0, 8)}`,
        domain: domain.trim().toLowerCase(),
        status: 'pending_dns',
        records: [{ type: 'CNAME', name: domain, value: `${app.name}.demo.invalid`, purpose: 'Routes traffic to the app (simulated)' }],
        message: 'Simulated: create the DNS records below.',
        created_at: new Date().toISOString(),
        checked_at: new Date().toISOString(),
      };
      domains.set(id, [...(domains.get(id) ?? []), created]);
      return structuredClone(created);
    },
    async checkDomain(id, domainId) {
      await deps.delay();
      deps.requireSession();
      const found = (domains.get(id) ?? []).find((d) => d.id === domainId);
      if (!found) throw new ApiError({ kind: 'http', status: 404, code: 'not_found' });
      found.checked_at = new Date().toISOString();
      return structuredClone(found);
    },
    async removeDomain(id, domainId) {
      await deps.delay();
      deps.requireSession();
      domains.set(id, (domains.get(id) ?? []).filter((d) => d.id !== domainId));
    },
    async listAlertChannels(signal) {
      await deps.delay(signal);
      deps.requireSession();
      return structuredClone(channels);
    },
    async createAlertChannel(input) {
      await deps.delay();
      deps.requireSession();
      const host = (() => {
        try {
          return new URL(input.url ?? '').hostname;
        } catch {
          return 'invalid';
        }
      })();
      const channel: AlertChannel = {
        id: `demo-ch-${randomId().slice(0, 8)}`,
        name: input.name,
        kind: input.kind,
        host,
        events: input.events,
        enabled: input.enabled,
        signed: input.kind === 'webhook',
        created_at: new Date().toISOString(),
        last_delivery_at: null,
        last_delivery_status: null,
      };
      channels.push(channel);
      return { ...structuredClone(channel), signing_secret: input.kind === 'webhook' ? `demo-${randomId()}` : null };
    },
    async updateAlertChannel(id, input) {
      await deps.delay();
      deps.requireSession();
      const channel = channels.find((c) => c.id === id);
      if (!channel) throw new ApiError({ kind: 'http', status: 404, code: 'not_found' });
      Object.assign(channel, { name: input.name, events: input.events, enabled: input.enabled });
      return structuredClone(channel);
    },
    async deleteAlertChannel(id) {
      await deps.delay();
      deps.requireSession();
      const index = channels.findIndex((c) => c.id === id);
      if (index >= 0) channels.splice(index, 1);
    },
    async testAlertChannel(id) {
      await deps.delay();
      deps.requireSession();
      const channel = channels.find((c) => c.id === id);
      if (!channel) throw new ApiError({ kind: 'http', status: 404, code: 'not_found' });
      channel.last_delivery_at = new Date().toISOString();
      channel.last_delivery_status = 'Simulated (not sent)';
      return { delivered: false, status: 'Simulated (not sent)' };
    },
    async listApiTokens(signal) {
      await deps.delay(signal);
      deps.requireSession();
      return structuredClone(tokens);
    },
    async createApiToken(input) {
      await deps.delay();
      deps.requireSession();
      const token = `cbt_demo${randomId()}`;
      const record: ApiToken = {
        id: `demo-tok-${randomId().slice(0, 8)}`,
        name: input.name,
        prefix: token.slice(0, 12),
        created_at: new Date().toISOString(),
        last_used_at: null,
        expires_at: input.expires_in_days ? new Date(Date.now() + input.expires_in_days * 86_400_000).toISOString() : null,
      };
      tokens.unshift(record);
      return { ...structuredClone(record), token };
    },
    async revokeApiToken(id) {
      await deps.delay();
      deps.requireSession();
      const index = tokens.findIndex((t) => t.id === id);
      if (index >= 0) tokens.splice(index, 1);
    },
  };
}
