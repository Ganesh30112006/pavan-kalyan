import { useCallback, useEffect, useRef, useState } from 'react';
import { isAbort, toApiError, type ApiError } from '../services/api/errors';

export interface ResourceOptions<T> {
  enabled?: boolean;
  /** Keep polling while this returns true for the latest data (e.g. a deployment is running). */
  pollWhile?: (data: T) => boolean;
  pollIntervalMs?: number;
  /** Polling stops after this long; the user can resume it. Keeps polling bounded. */
  maxPollDurationMs?: number;
  /** Consecutive failed polls tolerated before polling stops. */
  maxPollErrors?: number;
}

export type PollingState = 'idle' | 'active' | 'paused' | 'stopped';

export interface Resource<T> {
  status: 'loading' | 'success' | 'error';
  data: T | undefined;
  /** Error from the initial load (no data to show). */
  error: ApiError | null;
  /** Error from a refresh while older data is still shown (the data is stale). */
  refreshError: ApiError | null;
  isRefreshing: boolean;
  updatedAt: number | null;
  /** 'stopped' when the time or error limit ended polling before a terminal state. */
  polling: PollingState;
  reload(): void;
  resumePolling(): void;
  /** Replaces the data locally after a confirmed write. */
  setData(update: (current: T | undefined) => T | undefined): void;
}

interface State<T> {
  key: string;
  status: Resource<T>['status'];
  data: T | undefined;
  error: ApiError | null;
  refreshError: ApiError | null;
  isRefreshing: boolean;
  updatedAt: number | null;
  polling: PollingState;
}

function initial<T>(key: string): State<T> {
  return {
    key,
    status: 'loading',
    data: undefined,
    error: null,
    refreshError: null,
    isRefreshing: false,
    updatedAt: null,
    polling: 'idle',
  };
}

function isRetryable(error: ApiError): boolean {
  if (error.kind === 'network' || error.kind === 'timeout') return true;
  return error.kind === 'http' && (error.status === 429 || (error.status ?? 0) >= 500);
}

/**
 * Loads data for a page. Cancels the request on unmount or when `key`
 * changes, keeps the last good data when a refresh fails, and polls with a
 * bounded interval that pauses while the tab is hidden.
 */
export function useResource<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const enabled = options.enabled ?? true;
  const fetcherRef = useRef(fetcher);
  const optionsRef = useRef(options);
  const [state, setState] = useState<State<T>>(() => initial<T>(key));
  const [run, setRun] = useState(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
    optionsRef.current = options;
  });

  if (state.key !== key) {
    // A different resource: drop the old data so it is never shown under the new key.
    setState(initial<T>(key));
  }

  useEffect(() => {
    if (!enabled) return undefined;
    const controller = new AbortController();
    const startedAt = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let consecutiveErrors = 0;
    let lastData: T | undefined;
    let onVisible: (() => void) | undefined;

    const update = (fn: (s: State<T>) => State<T>) => {
      if (!controller.signal.aborted) setState((s) => (s.key === key ? fn(s) : s));
    };

    const scheduleNext = (data: T | undefined, delayFactor = 1) => {
      const opts = optionsRef.current;
      if (data === undefined || !opts.pollWhile || !opts.pollWhile(data)) {
        update((s) => ({ ...s, polling: 'idle' }));
        return;
      }
      if (Date.now() - startedAt > (opts.maxPollDurationMs ?? 15 * 60_000)) {
        update((s) => ({ ...s, polling: 'stopped' }));
        return;
      }
      update((s) => ({ ...s, polling: 'active' }));
      const interval = (opts.pollIntervalMs ?? 3000) * delayFactor;
      timer = setTimeout(() => {
        if (typeof document !== 'undefined' && document.hidden) {
          update((s) => ({ ...s, polling: 'paused' }));
          onVisible = () => {
            if (document.hidden) return;
            document.removeEventListener('visibilitychange', onVisible!);
            onVisible = undefined;
            void load(true);
          };
          document.addEventListener('visibilitychange', onVisible);
          return;
        }
        void load(true);
      }, interval);
    };

    const load = async (background: boolean) => {
      update((s) =>
        s.data !== undefined || background
          ? { ...s, isRefreshing: true }
          : { ...s, status: 'loading', error: null },
      );
      try {
        const data = await fetcherRef.current(controller.signal);
        if (controller.signal.aborted) return;
        consecutiveErrors = 0;
        lastData = data;
        update((s) => ({
          ...s,
          status: 'success',
          data,
          error: null,
          refreshError: null,
          isRefreshing: false,
          updatedAt: Date.now(),
        }));
        scheduleNext(data);
      } catch (e) {
        if (controller.signal.aborted || isAbort(e)) return;
        const error = toApiError(e);
        consecutiveErrors += 1;
        update((s) =>
          s.data !== undefined
            ? { ...s, refreshError: error, isRefreshing: false }
            : { ...s, status: 'error', error, isRefreshing: false },
        );
        const maxErrors = optionsRef.current.maxPollErrors ?? 5;
        if (lastData !== undefined && isRetryable(error) && consecutiveErrors < maxErrors) {
          scheduleNext(lastData, 2 ** consecutiveErrors);
        } else if (lastData !== undefined && optionsRef.current.pollWhile?.(lastData)) {
          update((s) => ({ ...s, polling: 'stopped' }));
        }
      }
    };

    void load(false);

    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
      if (onVisible) document.removeEventListener('visibilitychange', onVisible);
    };
  }, [key, run, enabled]);

  const reload = useCallback(() => setRun((n) => n + 1), []);
  const setData = useCallback(
    (fn: (current: T | undefined) => T | undefined) =>
      setState((s) => ({ ...s, data: fn(s.data), status: 'success', error: null, updatedAt: Date.now() })),
    [],
  );

  return {
    status: state.status,
    data: state.data,
    error: state.error,
    refreshError: state.refreshError,
    isRefreshing: state.isRefreshing,
    updatedAt: state.updatedAt,
    polling: state.polling,
    reload,
    resumePolling: reload,
    setData,
  };
}
