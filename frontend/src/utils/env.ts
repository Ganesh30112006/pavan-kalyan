/**
 * Build-time configuration. Only non-secret values may live here: every
 * VITE_* variable is compiled into the public bundle.
 */

function readBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL ?? '').trim();
  if (!raw) return '';
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';
    return url.toString().replace(/\/+$/, '');
  } catch {
    return '';
  }
}

export const API_BASE_URL = readBaseUrl();

export const SUPPORT_CONTACT = (import.meta.env.VITE_SUPPORT_URL ?? '').trim();
