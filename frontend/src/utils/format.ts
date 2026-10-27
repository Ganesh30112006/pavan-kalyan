import type { UserPreferences } from '../types/api';

const DASH = '—';

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function resolveTimeZone(timezone: string | null | undefined): string | undefined {
  if (!timezone) return undefined;
  try {
    new Intl.DateTimeFormat('en', { timeZone: timezone });
    return timezone;
  } catch {
    return undefined;
  }
}

export function formatDateTime(
  value: string | null | undefined,
  preferences?: UserPreferences | null,
): string {
  const date = parse(value);
  if (!date) return DASH;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: preferences ? preferences.time_format === '12h' : undefined,
    timeZone: resolveTimeZone(preferences?.timezone),
  }).format(date);
}

export function formatDate(value: string | null | undefined, preferences?: UserPreferences | null): string {
  const date = parse(value);
  if (!date) return DASH;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: resolveTimeZone(preferences?.timezone),
  }).format(date);
}

export function formatRelative(value: string | null | undefined, now: number = Date.now()): string {
  const date = parse(value);
  if (!date) return DASH;
  const seconds = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  // Small differences either way (clock skew, render timing) read as "just now".
  if (abs < 45) return 'just now';
  if (abs < 45 * 60) return rtf.format(Math.round(seconds / 60), 'minute');
  if (abs < 22 * 3600) return rtf.format(Math.round(seconds / 3600), 'hour');
  if (abs < 26 * 86400) return rtf.format(Math.round(seconds / 86400), 'day');
  if (abs < 320 * 86400) return rtf.format(Math.round(seconds / (30 * 86400)), 'month');
  return rtf.format(Math.round(seconds / (365 * 86400)), 'year');
}

export function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  const from = parse(start);
  const to = parse(end);
  if (!from || !to) return DASH;
  const total = Math.max(0, Math.round((to.getTime() - from.getTime()) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function formatCount(value: number, singular: string, plural = `${singular}s`): string {
  return `${value.toLocaleString()} ${value === 1 ? singular : plural}`;
}

/** "deployment.created" → "Deployment created" */
export function humanize(value: string): string {
  const text = value.replace(/[._:-]+/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : value;
}

/** Up to two initials from a display name, e.g. "Ada Lovelace" → "AL". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? [parts[0]![0], parts[parts.length - 1]![0]] : [parts[0]?.[0]];
  return letters.filter(Boolean).join('').toUpperCase() || '?';
}
