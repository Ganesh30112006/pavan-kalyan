import { createApplicationsApi } from './applicationsApi';
import { createAuditApi } from './auditApi';
import { createAuthApi } from './authApi';
import { createHttpClient, type HttpClientOptions } from './client';
import type { CloudBridgeApi } from './contract';
import { createDeploymentsApi } from './deploymentsApi';
import { createPlatformApi } from './platformApi';
import { createProvidersApi } from './providersApi';
import { createSettingsApi } from './settingsApi';

export type { CloudBridgeApi } from './contract';
export { ApiError, describeError, isAbort, isApiError, isMaintenance, toApiError } from './errors';

/** The production API: every call goes to the FastAPI backend. */
export function createHttpApi(options: HttpClientOptions): CloudBridgeApi {
  const http = createHttpClient(options);
  return {
    mode: 'live',
    auth: createAuthApi(http),
    applications: createApplicationsApi(http),
    deployments: createDeploymentsApi(http),
    providers: createProvidersApi(http),
    audit: createAuditApi(http),
    settings: createSettingsApi(http),
    platform: createPlatformApi(http),
    on: http.on,
  };
}
