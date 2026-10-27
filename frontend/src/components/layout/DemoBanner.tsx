import { FlaskConical } from 'lucide-react';
import { useDemoMode } from '../../hooks/useApi';
import './layout.css';

/** Shown on every screen while the simulated demo backend is in use. A landmark, so it is never stray content. */
export function DemoBanner() {
  const demo = useDemoMode();
  if (!demo) return null;
  return (
    <section className="demo-banner" aria-label="Demo mode">
      <FlaskConical size={14} aria-hidden="true" />
      DEMO MODE — simulated data only. No cloud provider is contacted and no resources are created.
    </section>
  );
}
