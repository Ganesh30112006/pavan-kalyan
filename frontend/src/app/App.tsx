import { RouterProvider, type createBrowserRouter } from 'react-router-dom';
import { AnnouncerProvider } from '../components/feedback/Announcer';
import { ToastProvider } from '../components/feedback/ToastProvider';
import { ErrorBoundary } from '../components/layout/ErrorBoundary';
import type { CloudBridgeApi } from '../services/api';
import { ApiProvider } from './ApiProvider';
import { AuthProvider } from './AuthProvider';

type Router = ReturnType<typeof createBrowserRouter>;

/** Last-resort fallback if the providers themselves fail; it has no router or API dependencies. */
function CrashFallback() {
  return (
    <main className="centered-page">
      <div className="centered-page__inner panel panel__body stack" role="alert">
        <h1>CloudBridge could not start</h1>
        <p className="text-muted">An unexpected error stopped the application from loading. Reload the page to try again.</p>
        <div>
          <button type="button" className="button button--primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    </main>
  );
}

export function App({ api, router }: { api: CloudBridgeApi; router: Router }) {
  return (
    <ErrorBoundary fallback={() => <CrashFallback />}>
      <ApiProvider api={api}>
        <AnnouncerProvider>
          <ToastProvider>
            <AuthProvider>
              <RouterProvider router={router} />
            </AuthProvider>
          </ToastProvider>
        </AnnouncerProvider>
      </ApiProvider>
    </ErrorBoundary>
  );
}
