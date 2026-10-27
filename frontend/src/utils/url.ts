const SAFE_EXTERNAL_PROTOCOLS = new Set(['https:']);
const SAFE_EXTERNAL_PROTOCOLS_DEV = new Set(['https:', 'http:']);

/**
 * Returns a normalised URL string when `value` is an absolute http(s) URL that
 * is safe to put in an href, otherwise null. Rejects javascript:, data:,
 * credentials in the URL and anything that does not parse.
 */
export function safeExternalUrl(value: string | null | undefined, allowHttp = false): string | null {
  if (!value) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  const allowed = allowHttp ? SAFE_EXTERNAL_PROTOCOLS_DEV : SAFE_EXTERNAL_PROTOCOLS;
  if (!allowed.has(parsed.protocol)) return null;
  if (parsed.username || parsed.password) return null;
  return parsed.toString();
}

/**
 * Accepts only same-origin, absolute paths ("/applications/abc"), used for
 * post-login redirects so a crafted link cannot send users to another site.
 */
export function safeInternalPath(value: unknown, fallback = '/'): string {
  if (typeof value !== 'string') return fallback;
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return fallback;
  if (/[\r\n\t]/.test(value)) return fallback;
  if (value.startsWith('/login') || value.startsWith('/logout')) return fallback;
  return value;
}

/** Validates a git repository URL. The backend still enforces SSRF controls. */
export function validateRepositoryUrl(value: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return 'Repository URL must be a full URL, for example https://github.com/org/repo.git';
  }
  if (parsed.protocol !== 'https:') return 'Repository URL must use https://';
  if (parsed.username || parsed.password) {
    return 'Repository URL must not include credentials. Repository access is configured on the backend.';
  }
  const host = parsed.hostname;
  if (
    host === 'localhost' ||
    /^\d{1,3}(\.\d{1,3}){3}$/.test(host) ||
    host.startsWith('[') ||
    !host.includes('.')
  ) {
    return 'Repository URL must use a public host name, not an IP address or local host.';
  }
  if (parsed.pathname.length < 2) return 'Repository URL must include the repository path, for example /org/repo.git';
  if (parsed.search || parsed.hash) return 'Repository URL must not include a query string or fragment.';
  return null;
}
