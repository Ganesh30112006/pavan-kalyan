import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { createAppRouter } from './app/router';
import { createHttpApi, type CloudBridgeApi } from './services/api';
import { API_BASE_URL } from './utils/env';
import { logger } from './utils/logger';
import './styles/variables.css';
import './styles/globals.css';
import './styles/layout.css';
import './styles/animations.css';
import './components/forms/forms.css';

async function createApi(): Promise<CloudBridgeApi> {
  // The choice is made once, at start-up. The app never switches to demo data
  // because a real request failed.
  // Written inline (not via utils/env) so a production build can drop the demo module entirely.
  if (import.meta.env.VITE_DEMO_MODE === 'true' && import.meta.env.MODE !== 'production') {
    const { createDemoApi } = await import('./services/demo/demoApi');
    return createDemoApi();
  }
  return createHttpApi({ baseUrl: API_BASE_URL });
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

// React would otherwise print full error objects (messages can include data) to the console.
const reportReactError = (kind: string) => (error: unknown) =>
  logger.error(`React ${kind} error`, { name: error instanceof Error ? error.name : typeof error });

createApi().then((api) => {
  createRoot(root, {
    onCaughtError: reportReactError('caught'),
    onUncaughtError: reportReactError('uncaught'),
    onRecoverableError: reportReactError('recoverable'),
  }).render(
    <StrictMode>
      <App api={api} router={createAppRouter()} />
    </StrictMode>,
  );
});
