import { array } from './decode';
import type { HttpClient } from './client';
import type { DeploymentsApi } from './contract';
import { deployment, deploymentEvent, deploymentSummary, logPage, paginated } from './decoders';
import { endpoints, listQuery, sortQuery } from './endpoints';

export function createDeploymentsApi(http: HttpClient): DeploymentsApi {
  return {
    list: (params, signal) =>
      http.request(
        {
          path: endpoints.deployments.list,
          query: {
            page: params.page,
            page_size: params.page_size,
            application_id: params.application_id,
            status: listQuery(params.status),
            provider: params.provider,
            search: params.search,
            sort: sortQuery(params.sort),
          },
          signal,
        },
        paginated(deploymentSummary),
      ),
    listForApplication: (applicationId, params, signal) =>
      http.request(
        {
          path: endpoints.apps.deployments(applicationId),
          query: { page: params.page, page_size: params.page_size },
          signal,
        },
        paginated(deploymentSummary),
      ),
    get: (id, signal) => http.request({ path: endpoints.deployments.one(id), signal }, deployment),
    listEvents: (id, signal) => http.request({ path: endpoints.deployments.events(id), signal }, array(deploymentEvent)),
    getLogs: (id, signal) => http.request({ path: endpoints.deployments.logs(id), signal }, logPage),
    create: (applicationId, { idempotencyKey }) =>
      http.request({ method: 'POST', path: endpoints.apps.deployments(applicationId), idempotencyKey }, deployment),
    cancel: (id) => http.request({ method: 'POST', path: endpoints.deployments.cancel(id) }, deployment),
    rollback: (id, { idempotencyKey }) =>
      http.request({ method: 'POST', path: endpoints.deployments.rollback(id), idempotencyKey }, deployment),
    approve: (id) => http.request({ method: 'POST', path: endpoints.deployments.approve(id) }, deployment),
    reject: (id) => http.request({ method: 'POST', path: endpoints.deployments.reject(id) }, deployment),
  };
}
