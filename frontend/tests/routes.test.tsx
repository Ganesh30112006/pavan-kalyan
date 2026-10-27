import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFakeApi, httpError } from './helpers/fakeApi';
import { makeSession } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

const AUTHENTICATED_ROUTES: Array<[string, RegExp]> = [
  ['/', /^Overview$/],
  ['/applications', /^Applications$/],
  ['/applications/new', /^Create application$/],
  ['/applications/app-1', /^orders-api$/],
  ['/applications/app-1/deployments', /^orders-api$/],
  ['/applications/app-1/configuration', /^orders-api$/],
  ['/applications/app-1/logs', /^orders-api$/],
  ['/deployments', /^Deployments$/],
  ['/deployments/dep-12345678abcd', /^Deployment /],
  ['/providers', /^Cloud providers$/],
  ['/providers/new', /^Connect a cloud provider$/],
  ['/providers/conn-1', /^Production AWS$/],
  ['/activity', /^Activity log$/],
  ['/settings/profile', /^Settings$/],
  ['/settings/organization', /^Settings$/],
  ['/settings/notifications', /^Settings$/],
  ['/settings/sessions', /^Settings$/],
  ['/settings/platform', /^Settings$/],
  ['/access-denied', /^Access denied$/],
  ['/no/such/page', /^Page not found$/],
];

describe('routing', () => {
  it.each(AUTHENTICATED_ROUTES)('renders %s', async (path, heading) => {
    renderApp(path);
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
  });

  it('redirects /settings to the profile page', async () => {
    const { router } = renderApp('/settings');
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/profile'));
  });

  it('sends signed-out users to the login page and back afterwards', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockRejectedValue(httpError(401));
    const { router, user } = renderApp('/deployments', api);
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'correct horse');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/deployments'));
  });

  it('renders the standalone error routes', async () => {
    renderApp('/error');
    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument();
  });

  it('shows the service-unavailable page when the session check cannot reach the backend', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockRejectedValueOnce(httpError(503)).mockResolvedValueOnce(makeSession());
    const { user } = renderApp('/', api);
    expect(await screen.findByRole('heading', { name: 'CloudBridge is unavailable' })).toBeInTheDocument();
    // No fallback sign-in or cached data is offered.
    expect(screen.queryByLabelText(/Password/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument();
  });

  it('shows the maintenance page for a maintenance response', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockRejectedValue(httpError(503, { code: 'maintenance' }));
    renderApp('/', api);
    expect(await screen.findByRole('heading', { name: 'CloudBridge is temporarily unavailable' })).toBeInTheDocument();
  });

  it('hides navigation the user has no permission for', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession([], { features: { organization_settings: false, notification_preferences: false } }));
    renderApp('/', api);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    expect(nav).not.toHaveTextContent('Activity log');
    expect(nav).not.toHaveTextContent('Organization');
    expect(nav).toHaveTextContent('Applications');
  });

  it('shows access denied for the activity log without permission', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession(['applications:create']));
    renderApp('/activity', api);
    expect(await screen.findByRole('heading', { name: 'Access denied' })).toBeInTheDocument();
    expect(api.audit.list).not.toHaveBeenCalled();
  });
});
