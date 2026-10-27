import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMutation } from '../src/hooks/useMutation';
import { useListParams } from '../src/hooks/usePaginationParams';
import { useResource } from '../src/hooks/useResource';
import { useSyncedSearch } from '../src/hooks/useSyncedSearch';
import { ApiError } from '../src/services/api/errors';

afterEach(() => vi.useRealTimers());

describe('useResource', () => {
  it('aborts the request when unmounted', async () => {
    let seen: AbortSignal | undefined;
    const { unmount } = renderHook(() =>
      useResource('k', (signal) => {
        seen = signal;
        return new Promise(() => undefined);
      }),
    );
    unmount();
    expect(seen?.aborted).toBe(true);
  });

  it('stops polling at a terminal state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetcher = vi.fn().mockResolvedValueOnce({ done: false }).mockResolvedValue({ done: true });
    const { result } = renderHook(() => useResource('k', fetcher, { pollWhile: (d: { done: boolean }) => !d.done, pollIntervalMs: 100 }));
    await waitFor(() => expect(result.current.data).toEqual({ done: false }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });
    await waitFor(() => expect(result.current.data).toEqual({ done: true }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.polling).toBe('idle');
  });

  it('stops polling after the time limit and can resume', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetcher = vi.fn().mockResolvedValue({ done: false });
    const { result } = renderHook(() =>
      useResource('k', fetcher, { pollWhile: (d: { done: boolean }) => !d.done, pollIntervalMs: 100, maxPollDurationMs: 250 }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.polling).toBe('stopped');
    const calls = fetcher.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetcher.mock.calls.length).toBe(calls);
    act(() => result.current.resumePolling());
    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(calls));
  });

  it('pauses polling while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    const fetcher = vi.fn().mockResolvedValue({ done: false });
    const { result } = renderHook(() => useResource('k', fetcher, { pollWhile: () => true, pollIntervalMs: 100 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.polling).toBe('paused');
    hidden.mockReturnValue(false);
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it('keeps stale data and reports the refresh error', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce('first').mockRejectedValueOnce(new ApiError({ kind: 'network' }));
    const { result } = renderHook(() => useResource('k', fetcher));
    await waitFor(() => expect(result.current.data).toBe('first'));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.refreshError?.kind).toBe('network'));
    expect(result.current.data).toBe('first');
    expect(result.current.status).toBe('success');
  });

  it('does not show data from a previous key', async () => {
    const fetcher = vi.fn((signal: AbortSignal) => {
      void signal;
      return Promise.resolve('a');
    });
    const { result, rerender } = renderHook(({ k }) => useResource(k, k === 'a' ? fetcher : () => new Promise<string>(() => undefined)), {
      initialProps: { k: 'a' },
    });
    await waitFor(() => expect(result.current.data).toBe('a'));
    rerender({ k: 'b' });
    expect(result.current.data).toBeUndefined();
    expect(result.current.status).toBe('loading');
  });
});

describe('useMutation', () => {
  it('ignores a second call while the first is pending', async () => {
    let resolve: (v: string) => void = () => undefined;
    const fn = vi.fn(() => new Promise<string>((r) => (resolve = r)));
    const { result } = renderHook(() => useMutation(fn));
    let first: Promise<unknown> = Promise.resolve();
    let second: Awaited<ReturnType<typeof result.current.run>> | undefined;
    await act(async () => {
      first = result.current.run();
      second = await result.current.run();
    });
    expect(second).toEqual({ ok: false, error: null });
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve('done');
      await first;
    });
    expect(result.current.succeeded).toBe(true);
  });

  it('returns errors rather than throwing and never retries', async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError({ kind: 'timeout', method: 'POST' }));
    const { result } = renderHook(() => useMutation(fn));
    let outcome: Awaited<ReturnType<typeof result.current.run>> | undefined;
    await act(async () => {
      outcome = await result.current.run();
    });
    expect(outcome).toMatchObject({ ok: false });
    expect(result.current.error?.kind).toBe('timeout');
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe('useSyncedSearch', () => {
  it('writes typing to the URL once it settles', async () => {
    const push = vi.fn();
    const { result } = renderHook(() => useSyncedSearch('', push, 50));
    act(() => result.current[1]('ord'));
    act(() => result.current[1]('orders'));
    await waitFor(() => expect(push).toHaveBeenCalledWith('orders'));
    expect(push).toHaveBeenCalledTimes(1);
  });

  it('follows a URL change made elsewhere without writing the old text back', async () => {
    const push = vi.fn();
    const { result, rerender } = renderHook(({ url }) => useSyncedSearch(url, push, 50), { initialProps: { url: 'zzz' } });
    rerender({ url: '' }); // e.g. "Clear search", back/forward or a link
    await waitFor(() => expect(result.current[0]).toBe(''));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(push).not.toHaveBeenCalled();
  });
});

describe('useListParams', () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={['/deployments?page=3']}>{children}</MemoryRouter>
  );
  const useList = () => ({
    list: useListParams(['q', 'sort', 'provider'] as const),
    search: useLocation().search,
    navigate: useNavigate(),
  });

  it('keeps both of two updates made before the next render', () => {
    const { result } = renderHook(useList, { wrapper });
    // A debounced search landing in the same tick as a sort click.
    act(() => {
      result.current.list.update({ q: 'orders' });
      result.current.list.update({ sort: 'application_name' });
    });
    expect(result.current.list.filters).toMatchObject({ q: 'orders', sort: 'application_name' });
    expect(result.current.list.page).toBe(1);
  });

  it('builds on the current URL after a navigation, not on an earlier update', () => {
    const { result } = renderHook(useList, { wrapper });
    act(() => result.current.list.update({ provider: 'aws_app_runner' }));
    act(() => result.current.navigate('/deployments?q=api'));
    act(() => result.current.list.update({ sort: 'status' }));
    expect(new URLSearchParams(result.current.search).toString()).toBe('q=api&sort=status');
  });
});
