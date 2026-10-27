import type { ProviderKind, SortParam } from '../../types/api';

/**
 * Every backend path in one place, so the contract can be adjusted without
 * touching the service modules. Paths are relative to VITE_API_BASE_URL.
 */
const enc = encodeURIComponent;
const V1 = '/api/v1';

export const endpoints = {
  auth: {
    session: `${V1}/auth/session`,
    login: `${V1}/auth/login`,
    logout: `${V1}/auth/logout`,
  },
  apps: {
    list: `${V1}/apps`,
    one: (id: string) => `${V1}/apps/${enc(id)}`,
    spec: (id: string) => `${V1}/apps/${enc(id)}/spec`,
    validate: (id: string) => `${V1}/apps/${enc(id)}/validate`,
    deployments: (id: string) => `${V1}/apps/${enc(id)}/deployments`,
    logs: (id: string) => `${V1}/apps/${enc(id)}/logs`,
    automation: (id: string) => `${V1}/apps/${enc(id)}/automation`,
    webhookSecret: (id: string) => `${V1}/apps/${enc(id)}/webhook-secret`,
    configFile: (id: string) => `${V1}/apps/${enc(id)}/config-file`,
    previews: (id: string) => `${V1}/apps/${enc(id)}/previews`,
    promote: (id: string) => `${V1}/apps/${enc(id)}/promote`,
    schedule: (id: string) => `${V1}/apps/${enc(id)}/schedule`,
    pause: (id: string) => `${V1}/apps/${enc(id)}/pause`,
    resume: (id: string) => `${V1}/apps/${enc(id)}/resume`,
    metrics: (id: string) => `${V1}/apps/${enc(id)}/metrics`,
    cost: (id: string) => `${V1}/apps/${enc(id)}/cost-estimate`,
    domains: (id: string) => `${V1}/apps/${enc(id)}/domains`,
    domain: (id: string, domainId: string) => `${V1}/apps/${enc(id)}/domains/${enc(domainId)}`,
    checkDomain: (id: string, domainId: string) => `${V1}/apps/${enc(id)}/domains/${enc(domainId)}/check`,
  },
  costEstimate: `${V1}/cost-estimate`,
  alertChannels: {
    list: `${V1}/alert-channels`,
    one: (id: string) => `${V1}/alert-channels/${enc(id)}`,
    test: (id: string) => `${V1}/alert-channels/${enc(id)}/test`,
  },
  apiTokens: {
    list: `${V1}/me/api-tokens`,
    one: (id: string) => `${V1}/me/api-tokens/${enc(id)}`,
  },
  deployments: {
    list: `${V1}/deployments`,
    one: (id: string) => `${V1}/deployments/${enc(id)}`,
    events: (id: string) => `${V1}/deployments/${enc(id)}/events`,
    logs: (id: string) => `${V1}/deployments/${enc(id)}/logs`,
    rollback: (id: string) => `${V1}/deployments/${enc(id)}/rollback`,
    cancel: (id: string) => `${V1}/deployments/${enc(id)}/cancel`,
    approve: (id: string) => `${V1}/deployments/${enc(id)}/approve`,
    reject: (id: string) => `${V1}/deployments/${enc(id)}/reject`,
  },
  providers: {
    capabilities: (provider: ProviderKind) => `${V1}/providers/${enc(provider)}/capabilities`,
    setup: (provider: ProviderKind) => `${V1}/providers/${enc(provider)}/setup`,
  },
  connections: {
    list: `${V1}/cloud-connections`,
    one: (id: string) => `${V1}/cloud-connections/${enc(id)}`,
    test: (id: string) => `${V1}/cloud-connections/${enc(id)}/test`,
  },
  audit: {
    list: `${V1}/audit-events`,
  },
  settings: {
    me: `${V1}/me`,
    notificationPreferences: `${V1}/me/notification-preferences`,
    organization: `${V1}/organization`,
    members: `${V1}/organization/members`,
  },
} as const;

/** `name` ascending, `-name` descending. */
export function sortQuery<K extends string>(sort: SortParam<K> | undefined): string | undefined {
  if (!sort) return undefined;
  return sort.direction === 'desc' ? `-${sort.key}` : sort.key;
}

/** Multi-value filters are sent comma-separated: `status=failed,failed_rollback`. */
export function listQuery(values: readonly string[] | undefined): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
}
