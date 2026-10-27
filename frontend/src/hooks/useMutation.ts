import { useCallback, useEffect, useRef, useState } from 'react';
import { toApiError, type ApiError } from '../services/api/errors';

export type MutationResult<T> =
  | { ok: true; data: T }
  /** error is null when the call was ignored because one was already running. */
  | { ok: false; error: ApiError | null };

export interface Mutation<TArgs extends unknown[], TResult> {
  run(...args: TArgs): Promise<MutationResult<TResult>>;
  reset(): void;
  isPending: boolean;
  error: ApiError | null;
  succeeded: boolean;
}

/**
 * Runs a state-changing request. A second call while one is pending is
 * ignored, which prevents duplicate submissions from double clicks. Errors are
 * returned (and kept in `error`) rather than thrown. Nothing is retried.
 */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
): Mutation<TArgs, TResult> {
  const fnRef = useRef(fn);
  const pendingRef = useRef(false);
  const mountedRef = useRef(true);
  const [isPending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [succeeded, setSucceeded] = useState(false);

  useEffect(() => {
    fnRef.current = fn;
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const run = useCallback(async (...args: TArgs): Promise<MutationResult<TResult>> => {
    if (pendingRef.current) return { ok: false, error: null };
    pendingRef.current = true;
    setPending(true);
    setError(null);
    setSucceeded(false);
    try {
      const data = await fnRef.current(...args);
      if (mountedRef.current) setSucceeded(true);
      return { ok: true, data };
    } catch (e) {
      const apiError = toApiError(e);
      if (mountedRef.current) setError(apiError);
      return { ok: false, error: apiError };
    } finally {
      pendingRef.current = false;
      if (mountedRef.current) setPending(false);
    }
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setSucceeded(false);
  }, []);

  return { run, reset, isPending, error, succeeded };
}
