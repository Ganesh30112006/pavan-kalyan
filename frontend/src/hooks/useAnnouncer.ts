import { useContext } from 'react';
import { AnnouncerContext, type AnnouncerContextValue } from '../app/contexts';

const noop: AnnouncerContextValue = { announce: () => undefined };

export function useAnnouncer(): AnnouncerContextValue {
  return useContext(AnnouncerContext) ?? noop;
}
