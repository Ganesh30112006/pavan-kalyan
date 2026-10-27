import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { PageLoader } from '../components/feedback/PageLoader';
import { useAuth } from '../hooks/useAuth';
import { MaintenancePage } from '../pages/errors/MaintenancePage';
import { ServiceUnavailablePage } from '../pages/errors/ServiceUnavailablePage';

/**
 * Route guard for signed-in pages. This only decides what to render; every
 * API endpoint must still authenticate and authorise the request itself.
 */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  switch (status) {
    case 'loading':
      return <PageLoader fullscreen label="Checking your session…" />;
    case 'anonymous':
    case 'expired':
      return (
        <Navigate
          to="/login"
          replace
          state={{ from: `${location.pathname}${location.search}`, reason: status === 'expired' ? 'expired' : undefined }}
        />
      );
    case 'unavailable':
      return <ServiceUnavailablePage />;
    case 'maintenance':
      return <MaintenancePage />;
    case 'authenticated':
      return <Outlet />;
  }
}
