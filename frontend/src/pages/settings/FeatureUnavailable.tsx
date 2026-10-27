import { PlugZap } from 'lucide-react';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Panel } from '../../components/layout/Panel';

/** Shown when the connected backend does not offer a settings area. */
export function FeatureUnavailable({ feature }: { feature: string }) {
  return (
    <Panel>
      <EmptyState
        icon={PlugZap}
        title={`${feature} are not available`}
        message={`The CloudBridge server you are connected to does not support ${feature.toLowerCase()}. Nothing can be configured here.`}
      />
    </Panel>
  );
}
