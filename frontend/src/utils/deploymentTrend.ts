import type { DeploymentSummary } from '../types/api';

export interface TrendDay {
  /** Local calendar date, yyyy-mm-dd. */
  date: string;
  succeeded: number;
  failed: number;
}

function localDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Outcome of a finished deployment, or null while it is still running (or cancelled). */
export function outcome(d: DeploymentSummary): 'succeeded' | 'failed' | null {
  if (d.status === 'succeeded') return 'succeeded';
  // A rollback that restored its target succeeded; a deployment that had to be rolled back did not.
  if (d.status === 'rolled_back') return d.kind === 'rollback' ? 'succeeded' : 'failed';
  if (d.status === 'failed' || d.status === 'failed_rollback') return 'failed';
  return null;
}

/**
 * Counts finished deployments per local day for the last `days` days (today included).
 * Only real records are counted; days without deployments are zero, never interpolated.
 */
export function deploymentTrend(deployments: DeploymentSummary[], days: number, now: Date = new Date()): TrendDay[] {
  const result: TrendDay[] = [];
  const index = new Map<string, TrendDay>();
  for (let i = days - 1; i >= 0; i -= 1) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    const entry = { date: localDate(day), succeeded: 0, failed: 0 };
    result.push(entry);
    index.set(entry.date, entry);
  }
  for (const d of deployments) {
    const kind = outcome(d);
    if (!kind) continue;
    const when = new Date(d.finished_at ?? d.created_at);
    if (Number.isNaN(when.getTime())) continue;
    const bucket = index.get(localDate(when));
    if (bucket) bucket[kind] += 1;
  }
  return result;
}
