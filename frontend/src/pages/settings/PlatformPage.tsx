import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { useDemoMode } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { API_BASE_URL } from '../../utils/env';
import { PROVIDERS, PROVIDER_KINDS } from '../../utils/providers';
import { SettingsNav } from './SettingsNav';

export function PlatformPage() {
  const demo = useDemoMode();
  const { session } = useAuth();

  return (
    <div className="page">
      <PageHeader
        title="Settings"
        documentTitle="Platform information"
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Platform' }]}
      />
      <SettingsNav />
      <Panel title="This installation">
        <dl className="description-list">
          <dt>Frontend version</dt>
          <dd className="mono">{__APP_VERSION__}</dd>
          <dt>Build</dt>
          <dd className="mono">{import.meta.env.MODE}</dd>
          <dt>Data source</dt>
          <dd>
            {demo ? (
              <>
                <span className="demo-tag">Demo mode</span> Simulated data in the browser. No backend or cloud provider is
                contacted.
              </>
            ) : (
              'CloudBridge API (live)'
            )}
          </dd>
          <dt>API base URL</dt>
          <dd className="mono">{demo ? 'Not used in demo mode' : API_BASE_URL || 'Same origin as this page'}</dd>
          <dt>API version</dt>
          <dd className="mono">/api/v1</dd>
        </dl>
      </Panel>
      <Panel title="Deployment providers">
        <dl className="description-list">
          {PROVIDER_KINDS.map((p) => (
            <div key={p} className="description-list__row">
              <dt>{PROVIDERS[p].label}</dt>
              <dd>Supported ({PROVIDERS[p].cloud})</dd>
            </div>
          ))}
          <dt>Azure Container Apps</dt>
          <dd className="text-muted">Not yet supported</dd>
        </dl>
      </Panel>
      {session && (
        <Panel title="Optional backend features">
          <dl className="description-list">
            <dt>Organization settings</dt>
            <dd>{session.features.organization_settings ? 'Available' : 'Not offered by this backend'}</dd>
            <dt>Notification preferences</dt>
            <dd>{session.features.notification_preferences ? 'Available' : 'Not offered by this backend'}</dd>
          </dl>
        </Panel>
      )}
    </div>
  );
}
