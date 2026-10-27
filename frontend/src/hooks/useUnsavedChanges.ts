import { useCallback, useEffect, useRef } from 'react';
import { useBlocker, type Blocker } from 'react-router-dom';

export interface UnsavedChanges {
  blocker: Blocker;
  /** Call right before navigating away after a successful save. */
  allowNavigation(): void;
}

/**
 * Asks before leaving a form with unsaved edits: in-app navigation is blocked
 * until the user confirms (render <UnsavedChangesDialog blocker={...} />), and
 * closing or reloading the tab triggers the browser's own prompt.
 */
export function useUnsavedChanges(isDirty: boolean): UnsavedChanges {
  const allowed = useRef(false);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty && !allowed.current && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!isDirty) return undefined;
    allowed.current = false;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [isDirty]);

  const allowNavigation = useCallback(() => {
    allowed.current = true;
  }, []);

  return { blocker, allowNavigation };
}
