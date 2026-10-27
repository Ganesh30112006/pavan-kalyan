import { useOutletContext } from 'react-router-dom';
import type { Resource } from '../../hooks/useResource';
import type { Application } from '../../types/api';

export interface ApplicationContext {
  application: Application;
  resource: Resource<Application>;
}

/** The application loaded by ApplicationLayout, for its tabs. */
export function useApplication(): ApplicationContext {
  return useOutletContext<ApplicationContext>();
}

export function sourceText(app: Application): string {
  if (app.source.type === 'image') return app.source.image ?? '—';
  const ref = app.source.ref_type === 'commit' ? `commit ${app.source.git_ref ?? '—'}` : (app.source.git_ref ?? 'default branch');
  return `${app.source.repository_url ?? '—'} @ ${ref}`;
}
