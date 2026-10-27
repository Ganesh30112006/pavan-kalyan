import { Lock } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button, ButtonLink } from '../../components/forms/Button';
import { ErrorPageLayout } from './ErrorPageLayout';

interface AccessDeniedProps {
  /** What the user tried to open, e.g. "the activity log". */
  resource?: string;
  requestId?: string | null;
}

export function AccessDeniedPage({ resource, requestId }: AccessDeniedProps) {
  const navigate = useNavigate();
  return (
    <ErrorPageLayout
      icon={Lock}
      code="Error 403"
      title="Access denied"
      message={
        <>
          Your account does not have permission to view {resource ?? 'this page'}. If you need access, ask an
          administrator of your organization to update your role.
        </>
      }
      requestId={requestId}
      actions={
        <>
          <ButtonLink variant="primary" to="/">
            Return to overview
          </ButtonLink>
          <Button onClick={() => navigate(-1)}>Go back</Button>
        </>
      }
    />
  );
}
