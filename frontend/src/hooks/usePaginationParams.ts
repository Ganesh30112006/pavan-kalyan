import { useCallback, useRef } from 'react';
import type { SortDirection, SortParam } from '../types/api';
import { useLocation, useSearchParams } from 'react-router-dom';

export const PAGE_SIZE_OPTIONS = [10, 25, 50] as const;

/**
 * Page, page size and filters live in the URL so lists survive reloads and can
 * be linked. Only non-sensitive values (search text, filters) go here.
 */
export function useListParams<K extends string>(filterKeys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const { key: locationKey } = useLocation();
  // React Router hands every functional update the params of the last render, so two updates before the
  // next render (a debounced search landing as a filter is clicked) would lose the first. Updates made
  // while the same location is rendered build on each other instead; any navigation has a new key.
  const pending = useRef<{ locationKey: string; next: URLSearchParams } | null>(null);
  const rawPage = Number(params.get('page'));
  const rawSize = Number(params.get('page_size'));
  const page = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const pageSize = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawSize) ? rawSize : 25;
  const filters = Object.fromEntries(filterKeys.map((k) => [k, params.get(k) ?? ''])) as Record<K, string>;

  const update = useCallback(
    (changes: Partial<Record<K | 'page' | 'page_size', string | number | null>>) => {
      setParams(
        (current) => {
          const base = pending.current?.locationKey === locationKey ? pending.current.next : current;
          const next = new URLSearchParams(base);
          let resetPage = false;
          for (const [key, value] of Object.entries(changes) as Array<[string, string | number | null]>) {
            if (value === null || value === '' || value === undefined) next.delete(key);
            else next.set(key, String(value));
            if (key !== 'page') resetPage = true;
          }
          if (resetPage && !('page' in changes)) next.delete('page');
          pending.current = { locationKey, next };
          return next;
        },
        { replace: true },
      );
    },
    [setParams, locationKey],
  );

  return { page, pageSize, filters, update };
}

/** Reads `sort=name` / `sort=-name` from the URL, accepting only known keys. */
export function parseSort<K extends string>(value: string, keys: readonly K[]): SortParam<K> | undefined {
  const desc = value.startsWith('-');
  const key = (desc ? value.slice(1) : value) as K;
  return keys.includes(key) ? { key, direction: desc ? 'desc' : 'asc' } : undefined;
}

export function serializeSort(sort: { key: string; direction: SortDirection }): string {
  return sort.direction === 'desc' ? `-${sort.key}` : sort.key;
}
