import { AlertOctagon } from 'lucide-react';
import { isRouteErrorResponse, useRouteError } from 'react-router-dom';
import { Button, ButtonLink } from '../../components/forms/Button';
import { SUPPORT_CONTACT } from '../../utils/env';
import { safeExternalUrl } from '../../utils/url';
import { ErrorPageLayout } from './ErrorPageLayout';
import { NotFoundPage } from './NotFoundPage';

function SupportLink() {
  const url = safeExternalUrl(SUPPORT_CONTACT);
  if (!url) return null;
  return (
    <a className="button" href={url} target="_blank" rel="noopener noreferrer">
      Contact support
    </a>
  );
}

/** Content for an unexpected client-side error. No technical details are shown. */
export function GeneralErrorContent({ onRetry, standalone = false }: { onRetry?: () => void; standalone?: boolean }) {
  return (
    <ErrorPageLayout
      icon={AlertOctagon}
      code="Unexpected error"
      title="Something went wrong"
      message="This page ran into a problem and could not be displayed. Your data has not been changed by this error."
      standalone={standalone}
      actions={
        <>
          <Button variant="primary" onClick={onRetry ?? (() => window.location.reload())}>
            Try again
          </Button>
          <ButtonLink to="/">Return to overview</ButtonLink>
          <SupportLink />
        </>
      }
    />
  );
}

export function GeneralErrorPage() {
  return <GeneralErrorContent standalone />;
}

/** Router-level error element: catches rendering errors outside the shell's boundary. */
export function RouteErrorPage() {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage standalone />;
  return <GeneralErrorContent standalone />;
}
