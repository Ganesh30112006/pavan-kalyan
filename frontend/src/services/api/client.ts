import type { FieldError } from '../../types/errors';
import { randomId } from '../../utils/id';
import { logger } from '../../utils/logger';
import { sanitizeServerMessage } from '../../utils/redact';
import { DecodeError, decode, isRecord, type Decoder } from './decode';
import { ApiError } from './errors';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: HttpMethod;
  path: string;
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Lets the backend recognise a repeated submission of the same write. */
  idempotencyKey?: string;
  /**
   * Login and session bootstrap: a 401 there means "not signed in", not
   * "session expired", so it must not trigger the global expiry handling.
   */
  authRequest?: boolean;
}

export type ApiEvent = 'session-expired' | 'maintenance';

export interface HttpClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  readTimeoutMs?: number;
  writeTimeoutMs?: number;
  /** Retries for idempotent reads only. Writes are never retried automatically. */
  maxReadRetries?: number;
  retryDelayMs?: number;
}

export interface HttpClient {
  /** Sends a request and decodes the JSON body. */
  request<T>(options: RequestOptions, decoder: Decoder<T>): Promise<T>;
  /** Sends a request whose response body is ignored (204, 202). Resolves with the status code. */
  send(options: RequestOptions): Promise<number>;
  /** Subscribes to session expiry and maintenance signals. */
  on(event: ApiEvent, listener: () => void): () => void;
}

const CSRF_COOKIE = 'cb_csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const REQUEST_ID_HEADER = 'X-Request-ID';
const MAX_RETRY_AFTER_SECONDS = 10;

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      try {
        return decodeURIComponent(trimmed.slice(prefix.length));
      } catch {
        return null;
      }
    }
  }
  return null;
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === '') continue;
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return `${baseUrl}${path}${qs ? `?${qs}` : ''}`;
}

function parseRetryAfter(header: string | null): number | null {
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(header);
  if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - Date.now()) / 1000));
  return null;
}

function isJson(response: Response): boolean {
  const type = response.headers.get('content-type') ?? '';
  return /^application\/(?:[\w.+-]+\+)?json\b/i.test(type);
}

function locToField(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const parts = loc.filter((p) => typeof p === 'string' || typeof p === 'number').map(String);
  if (parts[0] === 'body' || parts[0] === 'query' || parts[0] === 'path') parts.shift();
  return parts.length ? parts.join('.') : null;
}

interface ParsedErrorBody {
  code: string | null;
  message: string | null;
  requestId: string | null;
  fieldErrors: FieldError[];
}

/**
 * Accepts the CloudBridge envelope `{error: {code, message, request_id, fields}}`
 * and FastAPI's defaults `{detail: "..."}` / `{detail: [{loc, msg}]}`.
 * Anything else yields an empty description rather than an exception.
 */
export function parseErrorBody(body: unknown): ParsedErrorBody {
  const result: ParsedErrorBody = { code: null, message: null, requestId: null, fieldErrors: [] };
  if (!isRecord(body)) return result;

  const envelope = isRecord(body.error) ? body.error : null;
  if (envelope) {
    if (typeof envelope.code === 'string') result.code = envelope.code.slice(0, 64);
    result.message = sanitizeServerMessage(envelope.message);
    if (typeof envelope.request_id === 'string') result.requestId = envelope.request_id.slice(0, 128);
    if (Array.isArray(envelope.fields)) {
      for (const item of envelope.fields) {
        if (!isRecord(item) || typeof item.field !== 'string') continue;
        const message = sanitizeServerMessage(item.message);
        if (message) result.fieldErrors.push({ field: item.field, message });
      }
    }
    return result;
  }

  if (typeof body.detail === 'string') {
    result.message = sanitizeServerMessage(body.detail);
  } else if (Array.isArray(body.detail)) {
    for (const item of body.detail) {
      if (!isRecord(item)) continue;
      const field = locToField(item.loc);
      const message = sanitizeServerMessage(item.msg);
      if (field && message) result.fieldErrors.push({ field, message });
    }
  }
  return result;
}

async function readBody(response: Response): Promise<{ text: string; json: unknown; parsed: boolean }> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    return { text: '', json: undefined, parsed: false };
  }
  if (!text.trim()) return { text, json: undefined, parsed: false };
  try {
    return { text, json: JSON.parse(text), parsed: true };
  } catch {
    return { text, json: undefined, parsed: false };
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError({ kind: 'aborted' }));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new ApiError({ kind: 'aborted' }));
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export function createHttpClient(options: HttpClientOptions): HttpClient {
  const {
    baseUrl,
    readTimeoutMs = 15_000,
    writeTimeoutMs = 30_000,
    maxReadRetries = 2,
    retryDelayMs = 600,
  } = options;
  const listeners = new Map<ApiEvent, Set<() => void>>();

  function emit(event: ApiEvent) {
    listeners.get(event)?.forEach((listener) => listener());
  }

  async function attempt(opts: RequestOptions, requestId: string): Promise<{ response: Response; requestId: string }> {
    const method = opts.method ?? 'GET';
    const isWrite = method !== 'GET';
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(
      () => {
        timedOut = true;
        controller.abort();
      },
      opts.timeoutMs ?? (isWrite ? writeTimeoutMs : readTimeoutMs),
    );
    const onCallerAbort = () => controller.abort();
    if (opts.signal?.aborted) controller.abort();
    opts.signal?.addEventListener('abort', onCallerAbort, { once: true });

    const headers: Record<string, string> = {
      Accept: 'application/json',
      [REQUEST_ID_HEADER]: requestId,
    };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
    if (isWrite) {
      const csrf = readCookie(CSRF_COOKIE);
      if (csrf) headers[CSRF_HEADER] = csrf;
    }

    const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    try {
      const response = await fetchImpl(buildUrl(baseUrl, opts.path, opts.query), {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        credentials: 'include',
        cache: 'no-store',
        referrerPolicy: 'strict-origin-when-cross-origin',
        signal: controller.signal,
      });
      return { response, requestId: response.headers.get(REQUEST_ID_HEADER) ?? requestId };
    } catch {
      if (timedOut) throw new ApiError({ kind: 'timeout', requestId, method });
      if (opts.signal?.aborted) throw new ApiError({ kind: 'aborted', requestId, method });
      throw new ApiError({ kind: 'network', requestId, method });
    } finally {
      clearTimeout(timeout);
      opts.signal?.removeEventListener('abort', onCallerAbort);
    }
  }

  async function errorFromResponse(response: Response, requestId: string, opts: RequestOptions): Promise<ApiError> {
    const { json } = await readBody(response);
    const parsed = parseErrorBody(json);
    const error = new ApiError({
      kind: 'http',
      status: response.status,
      code: parsed.code,
      serverMessage: parsed.message,
      requestId: parsed.requestId ?? requestId,
      fieldErrors: parsed.fieldErrors,
      retryAfterSeconds: parseRetryAfter(response.headers.get('retry-after')),
      method: opts.method ?? 'GET',
    });
    if (response.status === 401 && !opts.authRequest) emit('session-expired');
    if (response.status === 503 && error.code === 'maintenance') emit('maintenance');
    return error;
  }

  function shouldRetryRead(error: ApiError): boolean {
    if (error.kind === 'network' || error.kind === 'timeout') return true;
    if (error.kind !== 'http') return false;
    if (error.status === 503 && error.code === 'maintenance') return false;
    if (error.status === 429) {
      return error.retryAfterSeconds === null || error.retryAfterSeconds <= MAX_RETRY_AFTER_SECONDS;
    }
    return error.status === 502 || error.status === 503 || error.status === 504;
  }

  async function execute(opts: RequestOptions): Promise<{ response: Response; requestId: string }> {
    const method = opts.method ?? 'GET';
    const retries = method === 'GET' ? maxReadRetries : 0;
    const requestId = randomId();
    for (let attemptNo = 0; ; attemptNo++) {
      try {
        const result = await attempt(opts, requestId);
        if (result.response.ok) return result;
        throw await errorFromResponse(result.response, result.requestId, opts);
      } catch (error) {
        if (!(error instanceof ApiError)) throw error;
        if (attemptNo >= retries || !shouldRetryRead(error)) throw error;
        const wait =
          error.retryAfterSeconds !== null
            ? error.retryAfterSeconds * 1000
            : retryDelayMs * 2 ** attemptNo;
        await sleep(wait, opts.signal);
      }
    }
  }

  return {
    async request<T>(opts: RequestOptions, decoder: Decoder<T>): Promise<T> {
      const { response, requestId } = await execute(opts);
      const method = opts.method ?? 'GET';
      if (!isJson(response)) {
        logger.warn('Response was not JSON', { path: opts.path, status: response.status, requestId });
        throw new ApiError({ kind: 'malformed', status: response.status, requestId, method });
      }
      const { json, parsed } = await readBody(response);
      if (!parsed) {
        throw new ApiError({ kind: 'malformed', status: response.status, requestId, method });
      }
      try {
        return decode(decoder, json);
      } catch (error) {
        if (error instanceof DecodeError) {
          logger.warn('Response did not match the API contract', {
            path: opts.path,
            at: error.path,
            expected: error.expected,
            requestId,
          });
          throw new ApiError({ kind: 'malformed', status: response.status, requestId, method });
        }
        throw error;
      }
    },

    async send(opts: RequestOptions): Promise<number> {
      const { response } = await execute(opts);
      // Drain the body so the connection can be reused; the content is not needed.
      await response.text().catch(() => undefined);
      return response.status;
    },

    on(event, listener) {
      let set = listeners.get(event);
      if (!set) {
        set = new Set();
        listeners.set(event, set);
      }
      set.add(listener);
      return () => set.delete(listener);
    },
  };
}
