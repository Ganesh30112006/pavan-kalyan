import { useState } from 'react';
import type { TrendDay } from '../../utils/deploymentTrend';
import './charts.css';

const BAR_SLOT = 24;
const BAR_WIDTH = 14;
const HEIGHT = 112;
const GAP = 2;
const RADIUS = 4;

function dayLabel(date: string): string {
  const [y, m, d] = date.split('-').map(Number);
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(y!, m! - 1, d!));
}

/** A rect with only its top corners rounded: the data end is rounded, the baseline end stays square. */
function topRounded(x: number, y: number, w: number, h: number): string {
  const r = Math.min(RADIUS, h, w / 2);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/**
 * Daily succeeded/failed deployment counts as stacked bars. Identity never relies on colour:
 * there is a legend, a per-bar tooltip, failures always sit on top, and the same data is
 * available as a table.
 */
export function DeploymentTrendChart({ days }: { days: TrendDay[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const max = Math.max(1, ...days.map((d) => d.succeeded + d.failed));
  const width = days.length * BAR_SLOT;
  const scale = (n: number) => (n / max) * (HEIGHT - 4);
  const totals = days.reduce((acc, d) => ({ succeeded: acc.succeeded + d.succeeded, failed: acc.failed + d.failed }), { succeeded: 0, failed: 0 });
  const summary = `${totals.succeeded} succeeded and ${totals.failed} failed deployments over ${days.length} days.`;
  const active = hovered === null ? null : days[hovered];

  return (
    <figure className="trend-chart">
      <div className="trend-chart__legend" aria-hidden="true">
        <span className="trend-chart__key trend-chart__key--succeeded" /> Succeeded ({totals.succeeded})
        <span className="trend-chart__key trend-chart__key--failed" /> Failed ({totals.failed})
      </div>
      <div className="trend-chart__plot" onMouseLeave={() => setHovered(null)}>
        <span className="trend-chart__max" aria-hidden="true">
          {max}
        </span>
        <svg viewBox={`0 0 ${width} ${HEIGHT}`} role="img" aria-label={`Deployment outcomes per day. ${summary}`}>
          <line x1="0" x2={width} y1={HEIGHT - scale(max)} y2={HEIGHT - scale(max)} className="trend-chart__grid" />
          <line x1="0" x2={width} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} className="trend-chart__baseline" />
          {days.map((d, i) => {
            const x = i * BAR_SLOT + (BAR_SLOT - BAR_WIDTH) / 2;
            const hs = scale(d.succeeded);
            const hf = scale(d.failed);
            const top = HEIGHT - hs - hf;
            return (
              <g key={d.date} className={hovered === i ? 'trend-chart__bar trend-chart__bar--hover' : 'trend-chart__bar'}>
                {/* Hit target covers the whole column, larger than the mark. */}
                <rect x={i * BAR_SLOT} y="0" width={BAR_SLOT} height={HEIGHT} className="trend-chart__hit" onMouseEnter={() => setHovered(i)} />
                {d.succeeded > 0 && (
                  <path
                    className="trend-chart__succeeded"
                    d={d.failed > 0 ? `M${x},${HEIGHT} V${HEIGHT - hs} H${x + BAR_WIDTH} V${HEIGHT} Z` : topRounded(x, HEIGHT - hs, BAR_WIDTH, hs)}
                  />
                )}
                {d.failed > 0 && <path className="trend-chart__failed" d={topRounded(x, top, BAR_WIDTH, Math.max(hf - (d.succeeded > 0 ? GAP : 0), 1))} />}
              </g>
            );
          })}
        </svg>
        {active && hovered !== null && (
          <div className="trend-chart__tooltip" style={{ left: `${((hovered + 0.5) / days.length) * 100}%` }} aria-hidden="true">
            <strong>{dayLabel(active.date)}</strong>
            <span>
              <span className="trend-chart__key trend-chart__key--succeeded" /> {active.succeeded} succeeded
            </span>
            <span>
              <span className="trend-chart__key trend-chart__key--failed" /> {active.failed} failed
            </span>
          </div>
        )}
      </div>
      <div className="trend-chart__axis" aria-hidden="true">
        <span>{days[0] ? dayLabel(days[0].date) : ''}</span>
        <span>Today</span>
      </div>
      <details className="trend-chart__table">
        <summary>View as table</summary>
        <table className="data-table">
          <caption className="visually-hidden">Deployment outcomes per day</caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Succeeded</th>
              <th scope="col">Failed</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.date}>
                <th scope="row">{dayLabel(d.date)}</th>
                <td>{d.succeeded}</td>
                <td>{d.failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
