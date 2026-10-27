import type { HttpClient } from './client';
import type { AuthApi } from './contract';
import { session } from './decoders';
import { endpoints } from './endpoints';

export function createAuthApi(http: HttpClient): AuthApi {
  return {
    getSession: (signal) => http.request({ path: endpoints.auth.session, signal, authRequest: true }, session),
    login: (input) =>
      http.request({ method: 'POST', path: endpoints.auth.login, body: input, authRequest: true }, session),
    logout: async () => {
      await http.send({ method: 'POST', path: endpoints.auth.logout, authRequest: true });
    },
  };
}
