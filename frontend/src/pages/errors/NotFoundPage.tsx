import { SearchX } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button, ButtonLink } from '../../components/forms/Button';
import { ErrorPageLayout } from './ErrorPageLayout';

export function NotFoundPage({ standalone = false }: { standalone?: boolean }) {
  const navigate = useNavigate();
  return (
    <ErrorPageLayout
      icon={SearchX}
      code="Error 404"
      title="Page not found"
      message="The page you requested does not exist. The link may be out of date, or the address may contain a typo."
      standalone={standalone}
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
