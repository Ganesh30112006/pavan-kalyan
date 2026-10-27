import { CircleDollarSign } from 'lucide-react';
import { useApi } from '../../../hooks/useApi';
import { useResource } from '../../../hooks/useResource';
import type { CostEstimateRequest } from '../../../types/api';

const money = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

/** The provider's list-price estimate for the chosen size and scaling. Shown for orientation only. */
export function CostPreview({ request }: { request: CostEstimateRequest }) {
  const api = useApi();
  const key = `cost-preview:${request.provider}:${request.cpu}:${request.memory}:${request.min_instances}:${request.max_instances}`;
  const cost = useResource(key, (signal) => api.platform.estimateCost(request, signal));
  if (cost.status !== 'success' || !cost.data) return null;
  const { monthly_low: low, monthly_high: high, assumptions } = cost.data;
  const range =
    low === null || high === null
      ? 'Runs on your cluster’s existing capacity'
      : low === high
        ? `About ${money.format(low)} per month`
        : `About ${money.format(low)} – ${money.format(high)} per month`;
  return (
    <section className="cost-preview" aria-label="Estimated cost">
      <p className="value-row">
        <CircleDollarSign size={16} aria-hidden="true" />
        <strong>{range}</strong>
      </p>
      <details>
        <summary className="text-small">How this is estimated</summary>
        <ul className="text-muted text-small">
          {assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
