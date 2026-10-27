import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AnnouncerContext } from '../../app/contexts';

/**
 * One polite live region for meaningful state changes ("Deployment
 * succeeded"). Pages announce transitions, not every poll, so screen-reader
 * users are not interrupted repeatedly.
 */
export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const announce = useCallback((text: string) => {
    // Clearing first makes screen readers announce a repeated message again.
    setMessage('');
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(text), 60);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  const value = useMemo(() => ({ announce }), [announce]);

  return (
    <AnnouncerContext.Provider value={value}>
      {children}
      <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true" data-testid="announcer">
        {message}
      </div>
    </AnnouncerContext.Provider>
  );
}
