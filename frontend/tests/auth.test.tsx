import { act, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../src/services/api/errors';
import { createFakeApi, httpError, pending } from './helpers/fakeApi';
import { renderApp } from './helpers/renderApp';

function signedOutApi() {
  const api = createFakeApi();
  api.auth.getSession.mockRejectedValue(httpError(401));
  return api;
}

describe('login', () => {
  it('validates the form before sending anything', async () => {
    const api = signedOutApi();
    const { user } = renderApp('/login', api);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));
    expect(screen.getAllByText('Email is required.').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Password is required.').length).toBeGreaterThan(0);
    expect(api.auth.login).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Email/)).toHaveAttribute('aria-invalid', 'true');
  });

  it('does not reveal which credential was wrong, and clears only the password', async () => {
    const api = signedOutApi();
    api.auth.login.mockRejectedValue(httpError(401, { method: 'POST' }));
    const { user } = renderApp('/login', api);
    await user.type(await screen.findByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('The email or password is incorrect.')).toBeInTheDocument();
    expect(screen.getByLabelText(/Email/)).toHaveValue('ada@example.com');
    expect(screen.getByLabelText(/Password/)).toHaveValue('');
  });

  it('explains rate limiting with the Retry-After time', async () => {
    const api = signedOutApi();
    api.auth.login.mockRejectedValue(httpError(429, { retryAfterSeconds: 30, method: 'POST' }));
    const { user } = renderApp('/login', api);
    await user.type(await screen.findByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'pw');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Too many requests')).toBeInTheDocument();
    expect(screen.getByText('Try again in 30 seconds.')).toBeInTheDocument();
  });

  it('keeps the input and explains a network failure', async () => {
    const api = signedOutApi();
    api.auth.login.mockRejectedValue(new ApiError({ kind: 'network', method: 'POST' }));
    const { user } = renderApp('/login', api);
    await user.type(await screen.findByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'pw');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Unable to reach CloudBridge')).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/)).toHaveValue('pw');
  });

  it('prevents duplicate submission while signing in', async () => {
    const api = signedOutApi();
    api.auth.login.mockReturnValue(pending());
    const { user } = renderApp('/login', api);
    await user.type(await screen.findByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'pw');
    const button = screen.getByRole('button', { name: 'Sign in' });
    await user.click(button);
    await user.click(button);
    await user.keyboard('{Enter}');
    expect(api.auth.login).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /Signing in/ })).toBeDisabled();
  });

  it('never writes credentials to browser storage', async () => {
    const api = signedOutApi();
    const { user } = renderApp('/login', api);
    await user.type(await screen.findByLabelText(/Email/), 'ada@example.com');
    await user.type(screen.getByLabelText(/Password/), 'super-secret-pw');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    await screen.findByRole('heading', { level: 1, name: 'Overview' });
    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie;
    expect(stored).not.toContain('super-secret-pw');
  });
});

describe('session expiry', () => {
  it('returns to the login page with an explanation when the session expires', async () => {
    const api = createFakeApi();
    const { router } = renderApp('/applications', api);
    await screen.findByRole('heading', { level: 1, name: 'Applications' });
    act(() => api.emit('session-expired'));
    expect(await screen.findByText('Your session has expired')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
  });
});

describe('logout', () => {
  it('signs out on the server before showing the signed-out screen', async () => {
    const api = createFakeApi();
    const { router } = renderApp('/logout', api);
    expect(await screen.findByText('You have signed out.')).toBeInTheDocument();
    expect(api.auth.logout).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe('/login');
  });

  it('says so when the server cannot confirm the sign-out', async () => {
    const api = createFakeApi();
    api.auth.logout.mockRejectedValueOnce(httpError(503, { method: 'POST' })).mockResolvedValueOnce(undefined);
    const { user } = renderApp('/logout', api);
    expect(await screen.findByRole('heading', { name: 'We could not confirm that you signed out' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(api.auth.logout).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('You have signed out.')).toBeInTheDocument();
  });
});
