import type { HttpClient } from './client';
import type { ApplicationsApi } from './contract';
import {
  application,
  applicationConfiguration,
  applicationSummary,
  logPage,
  paginated,
  specValidationResult,
} from './decoders';
import { endpoints, listQuery, sortQuery } from './endpoints';

export function createApplicationsApi(http: HttpClient): ApplicationsApi {
  return {
    list: (params, signal) =>
      http.request(
        {
          path: endpoints.apps.list,
          query: {
            page: params.page,
            page_size: params.page_size,
            search: params.search,
            status: params.status,
            health: listQuery(params.health),
            provider: params.provider,
            sort: sortQuery(params.sort),
          },
          signal,
        },
        paginated(applicationSummary),
      ),
    get: (id, signal) => http.request({ path: endpoints.apps.one(id), signal }, application),
    create: (input, { idempotencyKey }) =>
      http.request({ method: 'POST', path: endpoints.apps.list, body: input, idempotencyKey }, application),
    remove: async (id) =>
      (await http.send({ method: 'DELETE', path: endpoints.apps.one(id) })) === 202 ? 'accepted' : 'deleted',
    getConfiguration: (id, signal) => http.request({ path: endpoints.apps.spec(id), signal }, applicationConfiguration),
    updateConfiguration: (id, input) =>
      http.request({ method: 'PUT', path: endpoints.apps.spec(id), body: input }, applicationConfiguration),
    validate: (id) => http.request({ method: 'POST', path: endpoints.apps.validate(id) }, specValidationResult),
    getLogs: (id, signal) => http.request({ path: endpoints.apps.logs(id), signal }, logPage),
  };
}
