import { BarChart3 } from 'lucide-react';
import { useState } from 'react';
import { CardSkeleton } from '../../components/feedback/CardSkeleton';
import { EmptyState } from '../../components/feedback/EmptyState';
import { ErrorState } from '../../components/feedback/ErrorState';
import { Button } from '../../components/forms/Button';
import { Panel } from '../../components/layout/Panel';
import { useApi } from '../../hooks/useApi';
import { useResource } from '../../hooks/useResource';
import type { CostEstimate, MetricName, MetricRange, MetricSeries } from '../../types/api';
import { providerLabel } from '../../utils/providers';
import { useApplication } from './applicationContext';
import { TableScroll } from '../../components/tables/TableScroll';

const RANGES: { value: MetricRange; label: string }[] = [
  { value: '1h', label: '1 hour' },
  { value: '6h', label: '6 hours' },
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
];

const SERIES_TEXT: Record<MetricName, { title: string; summary: 'sum' | 'latest' }> = {
  requests: { title: 'Requests', summary: 'sum' },
  errors_4xx: { title: 'Client errors (4xx)', summary: 'sum' },
  errors_5xx: { title: 'Server errors (5xx)', summary: 'sum' },
  latency_ms: { title: 'Response time', summary: 'latest' },
  instances: { title: 'Instances', summary: 'latest' },
  cpu_percent: { title: 'CPU', summary: 'latest' },
  memory_percent: { title: 'Memory', summary: 'latest' },
};

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

function formatValue(value: number, unit: string): string {
  if (unit === 'percent') return `${number.format(value)}%`;
  if (unit === 'ms') return `${number.format(value)} ms`;
  return number.format(value);
}

function timeLabel(iso: string, range: MetricRange): string {
  const date = new Date(iso);
  return range === '7d'
    ? date.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
    : date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** One measure over time: a single line, the latest point marked, the numbers in a table for screen readers. */
function MetricCard({ series, range }: { series: MetricSeries; range: MetricRange }) {
  const text = SERIES_TEXT[series.name];
  const values = series.points.map((p) => p.value);
  const headline =
    values.length === 0
      ? '—'
      : formatValue(text.summary === 'sum' ? values.reduce((a, b) => a + b, 0) : values[values.length - 1]!, series.unit);
  const width = 240;
  const height = 56;
  const max = Math.max(...values, series.unit === 'percent' ? 100 : 0, 1);
  const x = (i: number) => (values.length <= 1 ? width : (i / (values.length - 1)) * width);
  const y = (v: number) => height - 2 - (v / max) * (height - 4);
  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = values.length - 1;
  const titleId = `metric-${series.name}`;

  return (
    <figure className="metric-card" aria-labelledby={titleId}>
      <h3 className="metric-card__title" id={titleId}>
        {text.title}
      </h3>
      <p className="metric-card__value">
        {headline}
        <span className="text-muted text-small"> {text.summary === 'sum' ? `in ${range}` : 'now'}</span>
      </p>
      {values.length > 0 ? (
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          <line className="metric-card__baseline" x1="0" x2={width} y1={height - 1} y2={height - 1} />
          {values.length > 1 && <path className="metric-card__area" d={`${line} L${width},${height - 1} L0,${height - 1} Z`} />}
          {values.length > 1 && <path className="metric-card__line" d={line} vectorEffect="non-scaling-stroke" />}
          <circle className="metric-card__dot" cx={x(last)} cy={y(values[last]!)} r="4" />
        </svg>
      ) : (
        <p className="text-muted text-small">No data points in this range.</p>
      )}
      <details>
        <summary className="text-small">Show data</summary>
        <TableScroll label={`${text.title} data`}>
          <table className="data-table">
            <caption className="visually-hidden">{text.title} over the last {range}</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Value</th>
              </tr>
            </thead>
            <tbody>
              {series.points.map((p) => (
                <tr key={p.timestamp}>
                  <td>{timeLabel(p.timestamp, range)}</td>
                  <td className="mono">{formatValue(p.value, series.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </details>
    </figure>
  );
}

function CostPanel({ cost }: { cost: CostEstimate }) {
  const range =
    cost.monthly_low === null || cost.monthly_high === null
      ? 'Billed through your cluster'
      : cost.monthly_low === cost.monthly_high
        ? money.format(cost.monthly_low)
        : `${money.format(cost.monthly_low)} – ${money.format(cost.monthly_high)}`;
  return (
    <div className="stack">
      <p className="metric-card__value">
        {range}
        {cost.monthly_low !== null && <span className="text-muted text-small"> per month (estimate)</span>}
      </p>
      {cost.lines.length > 0 && (
        <TableScroll label="Cost breakdown">
          <table className="data-table">
            <caption className="visually-hidden">Cost breakdown</caption>
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Low</th>
                <th scope="col">High</th>
              </tr>
            </thead>
            <tbody>
              {cost.lines.map((line) => (
                <tr key={line.item}>
                  <td>{line.item}</td>
                  <td className="mono">{money.format(line.low)}</td>
                  <td className="mono">{money.format(line.high)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      <ul className="text-muted text-small">
        {cost.assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </div>
  );
}

export function ApplicationMetricsTab() {
  const api = useApi();
  const { application } = useApplication();
  const [range, setRange] = useState<MetricRange>('1h');
  const metrics = useResource(`application:${application.id}:metrics:${range}`, (signal) =>
    api.platform.getMetrics(application.id, range, signal),
  );
  const cost = useResource(`application:${application.id}:cost`, (signal) => api.platform.getCost(application.id, signal));

  return (
    <div className="stack">
      <Panel
        title="Metrics"
        actions={
          <div className="range-picker" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <Button key={r.value} size="sm" aria-pressed={range === r.value} onClick={() => setRange(r.value)}>
                {r.label}
              </Button>
            ))}
          </div>
        }
      >
        {metrics.status === 'loading' ? (
          <CardSkeleton lines={4} label="Loading metrics" />
        ) : metrics.error ? (
          <ErrorState error={metrics.error} onRetry={metrics.reload} compact />
        ) : metrics.data?.unavailable_reason || !metrics.data?.series.length ? (
          <EmptyState
            icon={BarChart3}
            title="No metrics to show"
            message={metrics.data?.unavailable_reason ?? `${providerLabel(application.provider)} returned no data for this range.`}
            compact
          />
        ) : (
          <div className="metric-grid">
            {metrics.data.series.map((s) => (
              <MetricCard key={s.name} series={s} range={metrics.data!.range} />
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Estimated monthly cost">
        {cost.status === 'loading' ? (
          <CardSkeleton lines={2} label="Loading cost estimate" />
        ) : cost.error || !cost.data ? (
          <ErrorState error={cost.error} onRetry={cost.reload} compact />
        ) : (
          <CostPanel cost={cost.data} />
        )}
      </Panel>
    </div>
  );
}

