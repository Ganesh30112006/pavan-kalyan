import { Panel } from '../../components/layout/Panel';
import { LogViewer } from '../../components/status/LogViewer';
import { useApi } from '../../hooks/useApi';
import { useResource } from '../../hooks/useResource';
import { useApplication } from './applicationContext';

/** Runtime logs of the running service. Build logs belong to each deployment. */
export function ApplicationLogsTab() {
  const api = useApi();
  const { application } = useApplication();
  const logs = useResource(`application:${application.id}:logs`, (signal) => api.applications.getLogs(application.id, signal));

  return (
    <Panel title="Runtime logs">
      <p className="text-muted text-small">
        Output from the running service on {application.region}. Build and rollout logs are on each deployment's page; user
        actions are in the activity log.
      </p>
      <LogViewer
        logs={logs}
        label="Runtime logs"
        emptyMessage={application.current_version ? 'No log entries were returned.' : 'The application is not running, so it has no runtime logs yet.'}
        unsupportedMessage="The CloudBridge backend you are connected to does not provide runtime logs for applications."
      />
    </Panel>
  );
}
