import { vi } from 'vitest';
import type { CloudBridgeApi } from '../../src/services/api';
import { ApiError } from '../../src/services/api/errors';
import {
  makeAlertChannel,
  makeApiToken,
  makeAutomation,
  makeCost,
  makeDomain,
  makeMetrics,
  makeApp,
  makeAppSummary,
  makeAuditEvent,
  makeCapabilities,
  makeConfig,
  makeConnection,
  makeDeployment,
  makeSession,
  page,
} from './fixtures';

type Listener = () => void;

/** A controllable in-memory API for component tests. Every method is a vi.fn. */
export function createFakeApi(mode: 'live' | 'demo' = 'live') {
  const listeners = new Map<string, Set<Listener>>();
  const api = {
    mode,
    on: vi.fn((event: string, listener: Listener) => {
      const set = listeners.get(event) ?? new Set<Listener>();
      set.add(listener);
      listeners.set(event, set);
      return () => set.delete(listener);
    }),
    auth: {
      getSession: vi.fn<CloudBridgeApi['auth']['getSession']>(async () => makeSession()),
      login: vi.fn<CloudBridgeApi['auth']['login']>(async () => makeSession()),
      logout: vi.fn<CloudBridgeApi['auth']['logout']>(async () => undefined),
    },
    applications: {
      list: vi.fn<CloudBridgeApi['applications']['list']>(async () => page([makeAppSummary()])),
      get: vi.fn<CloudBridgeApi['applications']['get']>(async () => makeApp()),
      create: vi.fn<CloudBridgeApi['applications']['create']>(async () => makeApp({ id: 'app-new', name: 'new-app' })),
      remove: vi.fn<CloudBridgeApi['applications']['remove']>(async () => 'deleted' as const),
      getConfiguration: vi.fn<CloudBridgeApi['applications']['getConfiguration']>(async () => makeConfig()),
      updateConfiguration: vi.fn<CloudBridgeApi['applications']['updateConfiguration']>(async () => makeConfig({ version: 4 })),
      validate: vi.fn<CloudBridgeApi['applications']['validate']>(async () => ({ valid: true, issues: [], adjustments: [] })),
      getLogs: vi.fn<CloudBridgeApi['applications']['getLogs']>(async () => ({ entries: [], truncated: false })),
    },
    deployments: {
      list: vi.fn<CloudBridgeApi['deployments']['list']>(async () => page([makeDeployment({ status: 'succeeded' })])),
      listForApplication: vi.fn<CloudBridgeApi['deployments']['listForApplication']>(async () => page([makeDeployment({ status: 'succeeded' })])),
      get: vi.fn<CloudBridgeApi['deployments']['get']>(async () => makeDeployment()),
      listEvents: vi.fn<CloudBridgeApi['deployments']['listEvents']>(async () => []),
      getLogs: vi.fn<CloudBridgeApi['deployments']['getLogs']>(async () => ({ entries: [], truncated: false })),
      create: vi.fn<CloudBridgeApi['deployments']['create']>(async () => makeDeployment({ id: 'dep-new-000000', status: 'queued' })),
      cancel: vi.fn<CloudBridgeApi['deployments']['cancel']>(async () => makeDeployment({ status: 'building' })),
      rollback: vi.fn<CloudBridgeApi['deployments']['rollback']>(async () => makeDeployment({ id: 'dep-rb-0000000', kind: 'rollback', status: 'queued' })),
      approve: vi.fn<CloudBridgeApi['deployments']['approve']>(async () => makeDeployment({ status: 'queued', approved_by: 'Ada Admin' })),
      reject: vi.fn<CloudBridgeApi['deployments']['reject']>(async () => makeDeployment({ status: 'cancelled' })),
    },
    providers: {
      list: vi.fn<CloudBridgeApi['providers']['list']>(async () => [makeConnection()]),
      getCapabilities: vi.fn<CloudBridgeApi['providers']['getCapabilities']>(async (provider) => makeCapabilities({ provider })),
      getSetup: vi.fn<CloudBridgeApi['providers']['getSetup']>(async () => ({
        external_id: null,
        trusted_principal: 'arn:aws:iam::999999999999:role/cloudbridge',
        federation_issuer: 'https://abc.tokens.sts.global.api.aws',
        federation_audience: 'api://AzureADTokenExchange',
        aws: null,
      })),
      get: vi.fn<CloudBridgeApi['providers']['get']>(async () => makeConnection()),
      create: vi.fn<CloudBridgeApi['providers']['create']>(async () => makeConnection({ id: 'conn-new', status: 'pending_validation' })),
      validate: vi.fn<CloudBridgeApi['providers']['validate']>(async () => makeConnection({ status: 'validating', validation: { stage: 'connecting', started_at: null, finished_at: null, checks: [], error_code: null } })),
      remove: vi.fn<CloudBridgeApi['providers']['remove']>(async () => 'deleted' as const),
    },
    platform: {
      getAutomation: vi.fn<CloudBridgeApi['platform']['getAutomation']>(async () => makeAutomation()),
      updateAutomation: vi.fn<CloudBridgeApi['platform']['updateAutomation']>(async (_id, input) => makeAutomation(input)),
      rotateWebhookSecret: vi.fn<CloudBridgeApi['platform']['rotateWebhookSecret']>(async () => ({
        url: 'https://cloudbridge.example.com/api/v1/hooks/github/app-1',
        secret: 'whsec-test-000000000000',
      })),
      applyConfigFile: vi.fn<CloudBridgeApi['platform']['applyConfigFile']>(async () => makeConfig({ version: 5 })),
      listPreviews: vi.fn<CloudBridgeApi['platform']['listPreviews']>(async () => []),
      promote: vi.fn<CloudBridgeApi['platform']['promote']>(async () => makeDeployment({ id: 'dep-promo-00000', trigger: 'promotion', status: 'queued' })),
      getSchedule: vi.fn<CloudBridgeApi['platform']['getSchedule']>(async () => ({ schedule: null, paused: false, paused_by_schedule: false, asleep_now: false })),
      setSchedule: vi.fn<CloudBridgeApi['platform']['setSchedule']>(async (_id, schedule) => ({ schedule, paused: false, paused_by_schedule: false, asleep_now: false })),
      deleteSchedule: vi.fn<CloudBridgeApi['platform']['deleteSchedule']>(async () => ({ schedule: null, paused: false, paused_by_schedule: false, asleep_now: false })),
      pause: vi.fn<CloudBridgeApi['platform']['pause']>(async () => ({ schedule: null, paused: true, paused_by_schedule: false, asleep_now: false })),
      resume: vi.fn<CloudBridgeApi['platform']['resume']>(async () => ({ schedule: null, paused: false, paused_by_schedule: false, asleep_now: false })),
      getMetrics: vi.fn<CloudBridgeApi['platform']['getMetrics']>(async (_id, range) => makeMetrics(range)),
      getCost: vi.fn<CloudBridgeApi['platform']['getCost']>(async () => makeCost()),
      estimateCost: vi.fn<CloudBridgeApi['platform']['estimateCost']>(async (input) => makeCost({ provider: input.provider })),
      listDomains: vi.fn<CloudBridgeApi['platform']['listDomains']>(async () => []),
      addDomain: vi.fn<CloudBridgeApi['platform']['addDomain']>(async (_id, domain) => makeDomain({ domain })),
      checkDomain: vi.fn<CloudBridgeApi['platform']['checkDomain']>(async () => makeDomain({ status: 'active' })),
      removeDomain: vi.fn<CloudBridgeApi['platform']['removeDomain']>(async () => undefined),
      listAlertChannels: vi.fn<CloudBridgeApi['platform']['listAlertChannels']>(async () => []),
      createAlertChannel: vi.fn<CloudBridgeApi['platform']['createAlertChannel']>(async (input) => ({ ...makeAlertChannel({ name: input.name, kind: input.kind, events: input.events }), signing_secret: input.kind === 'webhook' ? 'sig-secret-0000' : null })),
      updateAlertChannel: vi.fn<CloudBridgeApi['platform']['updateAlertChannel']>(async (_id, input) => makeAlertChannel({ name: input.name, events: input.events, enabled: input.enabled })),
      deleteAlertChannel: vi.fn<CloudBridgeApi['platform']['deleteAlertChannel']>(async () => undefined),
      testAlertChannel: vi.fn<CloudBridgeApi['platform']['testAlertChannel']>(async () => ({ delivered: true, status: 'Delivered (200)' })),
      listApiTokens: vi.fn<CloudBridgeApi['platform']['listApiTokens']>(async () => []),
      createApiToken: vi.fn<CloudBridgeApi['platform']['createApiToken']>(async (input) => ({ ...makeApiToken({ name: input.name }), token: 'cbt_test-token-value' })),
      revokeApiToken: vi.fn<CloudBridgeApi['platform']['revokeApiToken']>(async () => undefined),
    },
    audit: {
      list: vi.fn<CloudBridgeApi['audit']['list']>(async () => page([makeAuditEvent()])),
    },
    settings: {
      updateProfile: vi.fn<CloudBridgeApi['settings']['updateProfile']>(async () => makeSession().user),
      getOrganization: vi.fn<CloudBridgeApi['settings']['getOrganization']>(async () => ({ id: 'o1', name: 'Acme', created_at: '2026-01-01T00:00:00Z' })),
      updateOrganization: vi.fn<CloudBridgeApi['settings']['updateOrganization']>(async (input) => ({ id: 'o1', name: input.name, created_at: '2026-01-01T00:00:00Z' })),
      listMembers: vi.fn<CloudBridgeApi['settings']['listMembers']>(async () => [
        { id: 'u1', display_name: 'Ada Admin', email: 'ada@example.com', role: 'admin' as const, joined_at: '2026-01-01T00:00:00Z' },
      ]),
      getNotificationPreferences: vi.fn<CloudBridgeApi['settings']['getNotificationPreferences']>(async () => ({
        email_enabled: true,
        events: {
          deployment_succeeded: true,
          deployment_failed: true,
          rollback_completed: false,
          provider_connection_failed: true,
          application_deleted: false,
          application_unhealthy: true,
          approval_requested: true,
        },
      })),
      updateNotificationPreferences: vi.fn<CloudBridgeApi['settings']['updateNotificationPreferences']>(async (input) => input),
    },
  } satisfies CloudBridgeApi;

  return Object.assign(api, {
    /** Simulates the HTTP client noticing a 401 or a maintenance response. */
    emit(event: 'session-expired' | 'maintenance') {
      listeners.get(event)?.forEach((l) => l());
    },
  });
}

export type FakeApi = ReturnType<typeof createFakeApi>;

export function httpError(status: number, init: Partial<ConstructorParameters<typeof ApiError>[0]> = {}): ApiError {
  return new ApiError({ kind: 'http', status, requestId: 'req-test-1', ...init });
}

/** A promise that never settles: keeps a request "in flight" for loading-state tests. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => undefined);
}
