import { LogOut, MonitorSmartphone } from 'lucide-react';
import { Button, ButtonLink } from '../../components/forms/Button';
import { PageHeader } from '../../components/layout/PageHeader';
import { Panel } from '../../components/layout/Panel';
import { useAuth } from '../../hooks/useAuth';
import { humanize } from '../../utils/format';
import { SettingsNav } from './SettingsNav';

export function SessionsPage() {
  const { session } = useAuth();

  return (
    <div className="page">
      <PageHeader
        title="Settings"
        documentTitle="Session"
        breadcrumbs={[{ label: 'Overview', to: '/' }, { label: 'Settings' }, { label: 'Session' }]}
      />
      <SettingsNav />
      {session && (
        <>
          <Panel title="Current session">
            <div className="stack">
              <dl className="description-list">
                <dt>Signed in as</dt>
                <dd>
                  {session.user.display_name} <span className="text-muted">({session.user.email})</span>
                </dd>
                <dt>Organization</dt>
                <dd>{session.organization.name}</dd>
                <dt>Role</dt>
                <dd>{humanize(session.user.role)}</dd>
                <dt>Permissions</dt>
                <dd>
                  {session.permissions.length === 0 ? (
                    <span className="text-muted">Read-only access</span>
                  ) : (
                    <ul className="plain-list mono text-small">
                      {session.permissions.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  )}
                </dd>
              </dl>
              <p className="text-muted text-small">
                Your session is held in a secure, HttpOnly cookie set by the CloudBridge server. The browser app never sees
                or stores a token, and permissions shown here only control what the interface offers: the server checks
                every request.
              </p>
              <div>
                <ButtonLink to="/logout" icon={<LogOut size={16} aria-hidden="true" />}>
                  Sign out
                </ButtonLink>
              </div>
            </div>
          </Panel>

          <Panel title="Other sessions">
            <div className="stack">
              <p className="text-muted">
                <MonitorSmartphone size={16} aria-hidden="true" /> Listing and revoking sessions on other devices is not
                supported by this version of the CloudBridge API.
              </p>
              <div>
                <Button disabled aria-describedby="revoke-unavailable">
                  Sign out of all other sessions
                </Button>
                <p id="revoke-unavailable" className="field__hint">
                  Unavailable: the backend has no endpoint for revoking other sessions.
                </p>
              </div>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
