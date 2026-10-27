import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFakeApi, httpError, pending } from './helpers/fakeApi';
import { makeAuditEvent, makeSession, page } from './helpers/fixtures';
import { renderApp } from './helpers/renderApp';

describe('overview', () => {
  it('shows real counts and keeps working sections when one section fails', async () => {
    const api = createFakeApi();
    api.audit.list.mockRejectedValue(httpError(500, { requestId: 'req-audit' }));
    renderApp('/', api);
    const summary = await screen.findByRole('region', { name: 'Summary' });
    await waitFor(() => expect(within(summary).getByRole('link', { name: /Total applications/ })).toHaveTextContent('1'));
    expect(within(summary).getByRole('link', { name: /Failed deployments/ })).toHaveAttribute('href', '/deployments?status=failed');
    expect(within(summary).getByRole('link', { name: /Healthy applications/ })).toHaveAttribute('href', '/applications?health=healthy');
    expect(await screen.findByText('Some sections could not be loaded. The rest of the page is up to date.')).toBeInTheDocument();
    expect(screen.getByText('req-audit')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Production AWS' })).toBeInTheDocument();
  });

  it('shows a getting-started checklist for a new organization', async () => {
    const api = createFakeApi();
    api.providers.list.mockResolvedValue([]);
    api.applications.list.mockResolvedValue(page([]));
    api.deployments.list.mockResolvedValue(page([]));
    renderApp('/', api);
    expect(await screen.findByRole('heading', { name: 'Get started' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Connect a cloud provider' })).toHaveAttribute('href', '/providers/new');
  });
});

describe('activity log', () => {
  it('lists audit events with request ids', async () => {
    renderApp('/activity');
    const table = await screen.findByRole('table', { name: 'Activity log' });
    expect(within(table).getByText('Deployment created')).toBeInTheDocument();
    expect(within(table).getByText('req-1')).toBeInTheDocument();
  });

  it('validates the date range without calling the API', async () => {
    const api = createFakeApi();
    renderApp('/activity?from=2026-10-10&to=2026-10-01', api);
    expect(await screen.findByText('The start date must be on or before the end date.')).toBeInTheDocument();
    expect(api.audit.list).not.toHaveBeenCalled();
  });

  it('offers to clear filters when nothing matches', async () => {
    const api = createFakeApi();
    api.audit.list.mockImplementation(async (params) => (params.outcome ? page([]) : page([makeAuditEvent()])));
    const { user } = renderApp('/activity?outcome=denied', api);
    await user.click(await screen.findByRole('button', { name: 'Clear search and filters' }));
    expect(await screen.findByRole('table', { name: 'Activity log' })).toBeInTheDocument();
  });
});

describe('settings', () => {
  it('saves the profile and updates the header', async () => {
    const api = createFakeApi();
    api.settings.updateProfile.mockImplementation(async (input) => ({ ...makeSession().user, display_name: input.display_name, preferences: input.preferences }));
    const { user } = renderApp('/settings/profile', api);
    const name = await screen.findByLabelText(/Display name/);
    await user.clear(name);
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(screen.getAllByText('Display name is required.').length).toBeGreaterThan(0);
    expect(api.settings.updateProfile).not.toHaveBeenCalled();
    await user.type(name, 'Ada Lovelace');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText('Settings updated')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Ada Lovelace/ })).toBeInTheDocument();
  });

  it('says organization settings are unavailable when the backend does not support them', async () => {
    const api = createFakeApi();
    api.auth.getSession.mockResolvedValue(makeSession(undefined, { features: { organization_settings: false, notification_preferences: false } }));
    renderApp('/settings/organization', api);
    expect(await screen.findByRole('heading', { name: 'Organization settings are not available' })).toBeInTheDocument();
    expect(api.settings.getOrganization).not.toHaveBeenCalled();
  });

  it('treats a 404 from the notifications endpoint as unsupported', async () => {
    const api = createFakeApi();
    api.settings.getNotificationPreferences.mockRejectedValue(httpError(404));
    renderApp('/settings/notifications', api);
    expect(await screen.findByRole('heading', { name: 'Notification preferences are not available' })).toBeInTheDocument();
  });

  it('saves notification preferences', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/settings/notifications', api);
    await user.click(await screen.findByLabelText('Rollback completed'));
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));
    await waitFor(() => expect(api.settings.updateNotificationPreferences).toHaveBeenCalledTimes(1));
    expect(api.settings.updateNotificationPreferences.mock.calls[0]?.[0].events.rollback_completed).toBe(true);
  });

  it('shows members read-only and lets administrators rename the organization', async () => {
    const api = createFakeApi();
    const { user } = renderApp('/settings/organization', api);
    expect(await screen.findByRole('table', { name: 'Organization members' })).toBeInTheDocument();
    const name = await screen.findByLabelText(/Organization name/);
    await user.clear(name);
    await user.type(name, 'Acme Cloud');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.settings.updateOrganization).toHaveBeenCalledWith({ name: 'Acme Cloud' }));
    expect(await screen.findByText('Acme Cloud', { selector: '.app-header__org' })).toBeInTheDocument();
  });
});

describe('unsaved changes', () => {
  it('asks before leaving a form with unsaved edits', async () => {
    const { user, router } = renderApp('/settings/profile');
    const name = await screen.findByLabelText(/Display name/);
    await user.type(name, ' Jr');
    await user.click(screen.getByRole('link', { name: 'Deployments' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Discard unsaved changes?' });
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
    expect(router.state.location.pathname).toBe('/settings/profile');
    expect(screen.getByLabelText(/Display name/)).toHaveValue('Ada Admin Jr');
    await user.click(screen.getByRole('link', { name: 'Deployments' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/deployments'));
  });
});

describe('loading states', () => {
  it.each([
    ['/deployments', 'Loading deployments…'],
    ['/providers', 'Loading provider connections…'],
    ['/activity', 'Loading activity…'],
  ])('%s shows a skeleton while loading', async (path, label) => {
    const api = createFakeApi();
    api.deployments.list.mockReturnValue(pending());
    api.providers.list.mockReturnValue(pending());
    api.audit.list.mockReturnValue(pending());
    renderApp(path, api);
    expect(await screen.findByText(label)).toBeInTheDocument();
  });
});
