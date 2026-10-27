import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ToastContext, type ToastContextValue, type ToastInput } from '../../app/contexts';
import { useDemoMode } from '../../hooks/useApi';
import { randomId } from '../../utils/id';
import { redact } from '../../utils/redact';
import { Toast, type ToastItem } from './Toast';
import './feedback.css';

const AUTO_DISMISS_MS = 6000;
const MAX_VISIBLE = 4;

/**
 * Transient notifications. Success and info messages dismiss themselves;
 * errors stay until dismissed. Pages show critical errors inline as well, so
 * nothing important exists only in a toast.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const demo = useDemoMode();
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const schedule = useCallback(
    (toast: ToastItem) => {
      if (toast.tone === 'error' || toast.persistent) return;
      timers.current.set(
        toast.id,
        setTimeout(() => dismiss(toast.id), AUTO_DISMISS_MS),
      );
    },
    [dismiss],
  );

  const notify = useCallback(
    (input: ToastInput) => {
      const toast: ToastItem = {
        ...input,
        title: redact(input.title),
        message: input.message ? redact(input.message) : undefined,
        id: randomId(),
      };
      setToasts((list) => {
        // The same message twice in a row is collapsed rather than stacked.
        const deduped = list.filter((t) => !(t.title === toast.title && t.message === toast.message));
        return [...deduped, toast].slice(-MAX_VISIBLE);
      });
      schedule(toast);
      return toast.id;
    },
    [schedule],
  );

  const pause = useCallback(
    (toast: ToastItem, paused: boolean) => {
      const timer = timers.current.get(toast.id);
      if (paused && timer) {
        clearTimeout(timer);
        timers.current.delete(toast.id);
      } else if (!paused && !timer) {
        schedule(toast);
      }
    },
    [schedule],
  );

  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((timer) => clearTimeout(timer));
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ notify, dismiss }), [notify, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <section className="toast-region" aria-label="Notifications">
        {toasts.map((toast) => (
          <Toast key={toast.id} toast={toast} demo={demo} onDismiss={dismiss} onPause={(p) => pause(toast, p)} />
        ))}
      </section>
    </ToastContext.Provider>
  );
}
