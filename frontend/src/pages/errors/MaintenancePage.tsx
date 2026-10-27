import { Wrench } from 'lucide-react';
import { Button } from '../../components/forms/Button';
import { useAuth } from '../../hooks/useAuth';
import { ErrorPageLayout } from './ErrorPageLayout';

export function MaintenancePage() {
  const { retry } = useAuth();
  return (
    <ErrorPageLayout
      icon={Wrench}
      code="Scheduled maintenance"
      title="CloudBridge is temporarily unavailable"
      message={
        <>
          <p>The service is undergoing maintenance. Deployments already running on your cloud providers are not affected.</p>
          <p>Try again in a few minutes.</p>
        </>
      }
      standalone
      actions={
        <Button variant="primary" onClick={retry}>
          Check again
        </Button>
      }
    />
  );
}
