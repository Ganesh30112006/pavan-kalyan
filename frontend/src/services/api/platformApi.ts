import { array } from './decode';
import type { HttpClient } from './client';
import type { PlatformApi } from './contract';
import {
  alertChannel,
  alertTest,
  apiToken,
  applicationConfiguration,
  applicationSummary,
  automation,
  costEstimate,
  createdAlertChannel,
  createdApiToken,
  customDomain,
  deployment,
  metrics,
  scheduleState,
  webhookSecret,
} from './decoders';
import { endpoints } from './endpoints';

export function createPlatformApi(http: HttpClient): PlatformApi {
  const apps = endpoints.apps;
  return {
    getAutomation: (id, signal) => http.request({ path: apps.automation(id), signal }, automation),
    updateAutomation: (id, input) => http.request({ method: 'PUT', path: apps.automation(id), body: input }, automation),
    rotateWebhookSecret: (id) => http.request({ method: 'POST', path: apps.webhookSecret(id) }, webhookSecret),
    applyConfigFile: (id, input) =>
      http.request({ method: 'PUT', path: apps.configFile(id), body: input }, applicationConfiguration),
    listPreviews: (id, signal) => http.request({ path: apps.previews(id), signal }, array(applicationSummary)),
    promote: (id, { idempotencyKey }) =>
      http.request({ method: 'POST', path: apps.promote(id), idempotencyKey }, deployment),
    getSchedule: (id, signal) => http.request({ path: apps.schedule(id), signal }, scheduleState),
    setSchedule: (id, schedule) => http.request({ method: 'PUT', path: apps.schedule(id), body: schedule }, scheduleState),
    deleteSchedule: (id) => http.request({ method: 'DELETE', path: apps.schedule(id) }, scheduleState),
    pause: (id) => http.request({ method: 'POST', path: apps.pause(id) }, scheduleState),
    resume: (id) => http.request({ method: 'POST', path: apps.resume(id) }, scheduleState),
    getMetrics: (id, range, signal) => http.request({ path: apps.metrics(id), query: { range }, signal }, metrics),
    getCost: (id, signal) => http.request({ path: apps.cost(id), signal }, costEstimate),
    estimateCost: (input, signal) =>
      http.request({ method: 'POST', path: endpoints.costEstimate, body: input, signal }, costEstimate),
    listDomains: (id, signal) => http.request({ path: apps.domains(id), signal }, array(customDomain)),
    addDomain: (id, domain) => http.request({ method: 'POST', path: apps.domains(id), body: { domain } }, customDomain),
    checkDomain: (id, domainId) => http.request({ method: 'POST', path: apps.checkDomain(id, domainId) }, customDomain),
    removeDomain: async (id, domainId) => {
      await http.send({ method: 'DELETE', path: apps.domain(id, domainId) });
    },
    listAlertChannels: (signal) => http.request({ path: endpoints.alertChannels.list, signal }, array(alertChannel)),
    createAlertChannel: (input) =>
      http.request({ method: 'POST', path: endpoints.alertChannels.list, body: input }, createdAlertChannel),
    updateAlertChannel: (id, input) =>
      http.request({ method: 'PUT', path: endpoints.alertChannels.one(id), body: input }, alertChannel),
    deleteAlertChannel: async (id) => {
      await http.send({ method: 'DELETE', path: endpoints.alertChannels.one(id) });
    },
    testAlertChannel: (id) => http.request({ method: 'POST', path: endpoints.alertChannels.test(id) }, alertTest),
    listApiTokens: (signal) => http.request({ path: endpoints.apiTokens.list, signal }, array(apiToken)),
    createApiToken: (input) =>
      http.request({ method: 'POST', path: endpoints.apiTokens.list, body: input }, createdApiToken),
    revokeApiToken: async (id) => {
      await http.send({ method: 'DELETE', path: endpoints.apiTokens.one(id) });
    },
  };
}
