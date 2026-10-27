/**
 * Removes credential-like material from text before it is shown or logged.
 * This is a last line of defence: the backend must never send secrets to the
 * browser in the first place.
 */

const REDACTED = '[redacted]';

const PATTERNS: ReadonlyArray<[RegExp, string]> = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, REDACTED],
  [/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g, REDACTED],
  [/\bAIza[0-9A-Za-z_-]{35}\b/g, REDACTED],
  [/\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b/g, REDACTED],
  [/\bBearer\s+[A-Za-z0-9\-._~+/]+=*/gi, `Bearer ${REDACTED}`],
  [/\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@:]+:[^\s/@]+@/gi, `$1${REDACTED}@`],
  [
    /(["']?\b(?:password|passwd|pwd|secret(?:_value)?|token|access_token|refresh_token|id_token|api[_-]?key|access[_-]?key(?:_id)?|secret[_-]?access[_-]?key|private[_-]?key|client[_-]?secret|session(?:_id)?|cookie|set-cookie|authorization|x-csrf-token)\b["']?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&}]+)/gi,
    `$1${REDACTED}`,
  ],
];

export function redact(text: string): string {
  let result = text;
  for (const [pattern, replacement] of PATTERNS) {
    result = result.replace(pattern, replacement);
  }
  return result;
}

/** Signs that a message is internal diagnostics rather than a user-facing explanation. */
const INTERNAL_DETAIL = [
  /Traceback \(most recent call last\)/i,
  /\bFile "[^"]+", line \d+/,
  /\n\s+at .+:\d+:\d+/,
  /\b(?:psycopg|sqlalchemy|asyncpg|botocore|google\.api_core|IntegrityError|OperationalError)\b/i,
  /\b(?:SELECT|INSERT|UPDATE|DELETE)\s.+\b(?:FROM|INTO|SET|WHERE)\b/i,
  /(?:^|[\s"'(])\/(?:usr|home|var|opt|srv|etc|app|tmp|root)\/\S+/,
  /[A-Za-z]:\\(?:[^\\\s]+\\)+/,
  /\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b/,
  /\b[a-z0-9-]+\.(?:internal|local|localdomain|svc\.cluster\.local)\b/i,
];

const MAX_MESSAGE_LENGTH = 300;

/**
 * Returns a server-provided message that is safe to show, or null when it
 * looks like internal diagnostics (stack traces, SQL, paths, internal hosts).
 */
export function sanitizeServerMessage(message: unknown): string | null {
  if (typeof message !== 'string') return null;
  const trimmed = message.trim();
  if (!trimmed) return null;
  if (INTERNAL_DETAIL.some((pattern) => pattern.test(trimmed))) return null;
  const clean = redact(trimmed).replace(/\s+/g, ' ');
  return clean.length > MAX_MESSAGE_LENGTH ? `${clean.slice(0, MAX_MESSAGE_LENGTH - 1)}…` : clean;
}
