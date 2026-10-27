import { useCallback, useRef } from 'react';
import { randomId } from '../utils/id';

/**
 * One idempotency key per distinct submission. Re-submitting the same payload
 * (e.g. after a timeout) reuses the key, so the backend can return the
 * original result instead of creating a duplicate. A changed payload gets a
 * new key.
 */
export function useIdempotencyKey() {
  const state = useRef<{ key: string; fingerprint: string | null }>({ key: '', fingerprint: null });
  return useCallback((payload: unknown): string => {
    const fingerprint = JSON.stringify(payload);
    if (state.current.fingerprint !== fingerprint || !state.current.key) {
      state.current = { key: randomId(), fingerprint };
    }
    return state.current.key;
  }, []);
}
