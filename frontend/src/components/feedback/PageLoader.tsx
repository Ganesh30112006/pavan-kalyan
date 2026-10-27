import { Spinner } from './InlineLoader';
import './feedback.css';

interface PageLoaderProps {
  label?: string;
  fullscreen?: boolean;
}

/** For screens whose layout is not known yet (e.g. the session check). Prefer skeletons otherwise. */
export function PageLoader({ label = 'Loading…', fullscreen = false }: PageLoaderProps) {
  return (
    <div className={`page-loader${fullscreen ? ' page-loader--fullscreen' : ''}`} role="status" aria-live="polite">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
