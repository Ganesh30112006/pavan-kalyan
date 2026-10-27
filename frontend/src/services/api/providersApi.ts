import { array } from './decode';
import type { HttpClient } from './client';
import type { ProvidersApi } from './contract';
import { connectionSetup, providerCapabilities, providerConnection } from './decoders';
import { endpoints } from './endpoints';

export function createProvidersApi(http: HttpClient): ProvidersApi {
  return {
    list: (signal) => http.request({ path: endpoints.connections.list, signal }, array(providerConnection)),
    getCapabilities: (provider, signal) =>
      http.request({ path: endpoints.providers.capabilities(provider), signal }, providerCapabilities),
    getSetup: (provider, signal) => http.request({ path: endpoints.providers.setup(provider), signal }, connectionSetup),
    get: (id, signal) => http.request({ path: endpoints.connections.one(id), signal }, providerConnection),
    create: (input, { idempotencyKey }) =>
      http.request(
        { method: 'POST', path: endpoints.connections.list, body: input, idempotencyKey },
        providerConnection,
      ),
    validate: (id) => http.request({ method: 'POST', path: endpoints.connections.test(id) }, providerConnection),
    remove: async (id) =>
      (await http.send({ method: 'DELETE', path: endpoints.connections.one(id) })) === 202 ? 'accepted' : 'deleted',
  };
}
