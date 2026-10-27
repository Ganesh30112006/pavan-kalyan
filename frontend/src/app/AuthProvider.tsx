import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useApi } from '../hooks/useApi';
import type { MutationResult } from '../hooks/useMutation';
import { isAbort, isMaintenance, toApiError, type ApiError } from '../services/api/errors';
import type { LoginRequest, Permission, Session, User } from '../types/api';
import { AuthContext, type AuthContextValue, type AuthStatus } from './contexts';

/**
 * Holds the session returned by the backend. The session lives in an
 * HttpOnly cookie that JavaScript cannot read; this provider only keeps the
 * user profile and permissions in memory and never writes them to storage.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [session, setSession] = useState<Session | null>(null);
  const [bootstrapError, setBootstrapError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const bootstrap = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    bootstrap.current = controller;
    api.auth
      .getSession(controller.signal)
      .then((s) => {
        setSession(s);
        setBootstrapError(null);
        setStatus('authenticated');
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted || isAbort(e)) return;
        const error = toApiError(e);
        setSession(null);
        if (error.status === 401) {
          setStatus('anonymous');
        } else if (isMaintenance(error)) {
          setStatus('maintenance');
        } else {
          setBootstrapError(error);
          setStatus('unavailable');
        }
      });
    return () => controller.abort();
  }, [api, attempt]);

  useEffect(
    () =>
      api.on('session-expired', () => {
        setSession(null);
        setStatus((current) => (current === 'authenticated' ? 'expired' : current));
      }),
    [api],
  );

  useEffect(() => api.on('maintenance', () => setStatus('maintenance')), [api]);

  const login = useCallback(
    async (input: LoginRequest): Promise<MutationResult<Session>> => {
      bootstrap.current?.abort();
      try {
        const s = await api.auth.login(input);
        setSession(s);
        setBootstrapError(null);
        setStatus('authenticated');
        return { ok: true, data: s };
      } catch (e) {
        return { ok: false, error: toApiError(e) };
      }
    },
    [api],
  );

  const logout = useCallback(async (): Promise<MutationResult<void>> => {
    bootstrap.current?.abort();
    try {
      await api.auth.logout();
    } catch (e) {
      const error = toApiError(e);
      // 401 means the server already considers the session ended.
      if (error.status !== 401) return { ok: false, error };
    }
    setSession(null);
    setStatus('anonymous');
    return { ok: true, data: undefined };
  }, [api]);

  const retry = useCallback(() => {
    setStatus('loading');
    setAttempt((n) => n + 1);
  }, []);

  const can = useCallback((permission: Permission) => Boolean(session?.permissions.includes(permission)), [session]);

  const updateUser = useCallback((user: User) => setSession((s) => (s ? { ...s, user } : s)), []);
  const updateOrganizationName = useCallback(
    (name: string) => setSession((s) => (s ? { ...s, organization: { ...s.organization, name } } : s)),
    [],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ status, session, bootstrapError, login, logout, retry, can, updateUser, updateOrganizationName }),
    [status, session, bootstrapError, login, logout, retry, can, updateUser, updateOrganizationName],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
