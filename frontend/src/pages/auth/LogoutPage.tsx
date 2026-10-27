import { LogOut } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLoader } from '../../components/feedback/PageLoader';
import { Button, ButtonLink } from '../../components/forms/Button';
import { useAuth } from '../../hooks/useAuth';
import type { MutationResult } from '../../hooks/useMutation';
import { describeError, type ApiError } from '../../services/api/errors';
import { ErrorPageLayout } from '../errors/ErrorPageLayout';

/**
 * Signs out on the server. If the server cannot confirm, the user is told so
 * instead of being shown a signed-out screen while the session may still be valid.
 */
export function LogoutPage() {
  const { logout, status } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<ApiError | null>(null);
  const started = useRef(false);

  const finish = useCallback(
    (result: MutationResult<void>) => {
      if (result.ok) navigate('/login', { replace: true, state: { reason: 'signed_out' } });
      else if (result.error) setError(result.error);
    },
    [navigate],
  );

  useEffect(() => {
    // Wait for the session check; a late result must not undo the sign-out.
    if (started.current || status === 'loading') return;
    started.current = true;
    if (status === 'anonymous' || status === 'expired') {
      navigate('/login', { replace: true });
      return;
    }
    void logout().then(finish);
  }, [logout, finish, status, navigate]);

  const retry = () => {
    setError(null);
    void logout().then(finish);
  };

  if (!error) return <PageLoader fullscreen label="Signing out…" />;

  const description = describeError(error);
  return (
    <ErrorPageLayout
      icon={LogOut}
      code="Sign-out not confirmed"
      title="We could not confirm that you signed out"
      message={
        <>
          <p>{description.message}</p>
          <p>Your session may still be active. Try again, or close the browser if you are on a shared device.</p>
        </>
      }
      requestId={error.requestId}
      standalone
      actions={
        <>
          <Button
            variant="primary"
            onClick={retry}
          >
            Try again
          </Button>
          <ButtonLink to="/">Return to CloudBridge</ButtonLink>
        </>
      }
    />
  );
}
