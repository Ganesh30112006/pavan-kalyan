import { LogIn, Network } from 'lucide-react';
import { useMemo, useRef, useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Alert } from '../../components/feedback/Alert';
import { PageLoader } from '../../components/feedback/PageLoader';
import { Button } from '../../components/forms/Button';
import { TextField } from '../../components/forms/Field';
import { FormErrorSummary } from '../../components/forms/FormErrorSummary';
import { useFormState } from '../../components/forms/useFormState';
import { DemoBanner } from '../../components/layout/DemoBanner';
import { useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { ApiError } from '../../services/api/errors';
import { safeInternalPath } from '../../utils/url';
import { compose, email, maxLength, required } from '../../utils/validation';
import '../../components/layout/layout.css';
import './auth.css';

interface LoginState {
  from?: string;
  reason?: 'expired' | 'signed_out';
}

export function LoginPage() {
  useDocumentTitle('Sign in');
  const { status, login, bootstrapError } = useAuth();
  const demo = useDemoMode();
  const location = useLocation();
  const navigate = useNavigate();
  const state = (location.state ?? {}) as LoginState;
  const destination = safeInternalPath(state.from, '/');
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const summaryRef = useRef<HTMLDivElement>(null);

  const validators = useMemo(
    () => ({
      email: compose(required('Email'), email, maxLength('Email', 254)),
      password: compose(required('Password'), maxLength('Password', 1024)),
    }),
    [],
  );
  const form = useFormState({ email: '', password: '' }, validators);

  if (status === 'loading') return <PageLoader fullscreen label="Checking your session…" />;
  if (status === 'authenticated') return <Navigate to={destination} replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending) return;
    form.setSubmitted(true);
    if (!form.isValid) {
      summaryRef.current?.focus();
      return;
    }
    setPending(true);
    setError(null);
    const result = await login({ email: form.values.email.trim(), password: form.values.password });
    setPending(false);
    if (result.ok) {
      navigate(destination, { replace: true });
      return;
    }
    if (!result.error) return;
    const failure = result.error;
    if (failure.status === 401) {
      // Do not reveal whether the email exists.
      setError(
        new ApiError({
          kind: 'http',
          status: 400,
          serverMessage: 'The email or password is incorrect.',
          requestId: failure.requestId,
          method: 'POST',
        }),
      );
      form.setValue('password', '');
    } else {
      setError(failure);
      form.applyServerErrors(failure);
    }
    requestAnimationFrame(() => summaryRef.current?.focus());
  };

  const clientMessages = form.submitted ? Object.values(form.clientErrors).filter((m): m is string => Boolean(m)) : [];

  return (
    <>
      <DemoBanner />
      <main className="centered-page">
        <div className="centered-page__inner">
          <div className="standalone-brand">
            <span className="app-header__logo" aria-hidden="true">
              <Network size={16} />
            </span>
            CloudBridge
          </div>
          <form className="auth-card" onSubmit={onSubmit} noValidate aria-labelledby="login-title">
            <div className="auth-card__header">
              <h1 id="login-title">Sign in</h1>
              <p className="text-muted">Manage applications across AWS App Runner and Google Cloud Run.</p>
            </div>

            {state.reason === 'expired' && (
              <Alert tone="warning" title="Your session has expired" role="status">
                Sign in again to continue where you left off.
              </Alert>
            )}
            {state.reason === 'signed_out' && (
              <Alert tone="success" role="status">
                You have signed out.
              </Alert>
            )}
            {status === 'unavailable' && !error && (
              <Alert tone="warning" title="CloudBridge could not be reached" role="status" requestId={bootstrapError?.requestId}>
                Signing in may fail until the service is available again.
              </Alert>
            )}
            {demo && (
              <Alert tone="info" title="Demo mode">
                Any email address and password are accepted. Data is simulated.
              </Alert>
            )}

            <FormErrorSummary ref={summaryRef} error={error} fieldMessages={error ? [] : clientMessages} />

            <TextField
              label="Email"
              type="email"
              name="email"
              autoComplete="username"
              inputMode="email"
              required
              value={form.values.email}
              onChange={(e) => form.setValue('email', e.target.value)}
              onBlur={() => form.touch('email')}
              error={form.errorFor('email')}
              disabled={pending}
            />
            <TextField
              label="Password"
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={form.values.password}
              onChange={(e) => form.setValue('password', e.target.value)}
              onBlur={() => form.touch('password')}
              error={form.errorFor('password')}
              disabled={pending}
            />
            <Button
              type="submit"
              variant="primary"
              loading={pending}
              loadingLabel="Signing in…"
              disabled={form.submitted && !form.isValid}
              icon={<LogIn size={16} aria-hidden="true" />}
            >
              Sign in
            </Button>
          </form>
          <p className="auth-footer">Access is managed by your organization administrator.</p>
        </div>
      </main>
    </>
  );
}
