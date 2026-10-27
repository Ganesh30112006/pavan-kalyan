import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHttpClient, parseErrorBody } from '../src/services/api/client';
import { object, string } from '../src/services/api/decode';
import { ApiError } from '../src/services/api/errors';

const thing = object<{ id: string }>({ id: string });

function json(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
}

function client(fetchImpl: typeof fetch, extra = {}) {
  return createHttpClient({ baseUrl: 'http://api.test', fetchImpl, retryDelayMs: 1, ...extra });
}

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error('expected rejection');
}

afterEach(() => {
  document.cookie = 'cb_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
});

describe('http client', () => {
  it('decodes a valid JSON response and sends credentials and a request id', async () => {
    const fetchImpl = vi.fn(async () => json({ id: 'a', extra: 'ignored' }));
    const result = await client(fetchImpl).request({ path: '/x', query: { page: 2, search: '', q: undefined } }, thing);
    expect(result).toEqual({ id: 'a' });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://api.test/x?page=2');
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>)['X-Request-ID']).toBeTruthy();
  });

  it('treats invalid JSON as a malformed response', async () => {
    const fetchImpl = vi.fn(async () => new Response('{nope', { headers: { 'content-type': 'application/json' } }));
    const error = await rejection(client(fetchImpl).request({ path: '/x' }, thing));
    expect(error.kind).toBe('malformed');
  });

  it('treats an HTML 200 response as malformed, not as data', async () => {
    const fetchImpl = vi.fn(async () => new Response('<html>login</html>', { headers: { 'content-type': 'text/html' } }));
    expect((await rejection(client(fetchImpl).request({ path: '/x' }, thing))).kind).toBe('malformed');
  });

  it('treats an empty body as malformed when data was expected', async () => {
    const fetchImpl = vi.fn(async () => new Response('', { headers: { 'content-type': 'application/json' } }));
    expect((await rejection(client(fetchImpl).request({ path: '/x' }, thing))).kind).toBe('malformed');
  });

  it('rejects responses that do not match the contract', async () => {
    const fetchImpl = vi.fn(async () => json({ id: 42 }));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect((await rejection(client(fetchImpl).request({ path: '/x' }, thing))).kind).toBe('malformed');
    expect(warn).toHaveBeenCalled();
  });

  it('maps the CloudBridge error envelope, keeping the request id', async () => {
    const fetchImpl = vi.fn(async () =>
      json(
        { error: { code: 'duplicate_name', message: 'Name taken', request_id: 'req-9', fields: [{ field: 'name', message: 'In use' }] } },
        { status: 409 },
      ),
    );
    const error = await rejection(client(fetchImpl).request({ method: 'POST', path: '/x' }, thing));
    expect(error).toMatchObject({ kind: 'http', status: 409, code: 'duplicate_name', serverMessage: 'Name taken', requestId: 'req-9' });
    expect(error.fieldErrors).toEqual([{ field: 'name', message: 'In use' }]);
  });

  it('handles error responses with no body or a non-JSON body', async () => {
    const empty = vi.fn(async () => new Response(null, { status: 500 }));
    expect(await rejection(client(empty, { maxReadRetries: 0 }).request({ path: '/x' }, thing))).toMatchObject({ kind: 'http', status: 500, serverMessage: null });
    const html = vi.fn(async () => new Response('<h1>Bad gateway</h1>', { status: 502, headers: { 'content-type': 'text/html' } }));
    expect(await rejection(client(html, { maxReadRetries: 0 }).request({ path: '/x' }, thing))).toMatchObject({ status: 502, serverMessage: null });
  });

  it('drops server messages that contain internal details', async () => {
    const fetchImpl = vi.fn(async () =>
      json({ detail: 'Traceback (most recent call last):\n  File "/app/main.py", line 3' }, { status: 400 }),
    );
    const error = await rejection(client(fetchImpl).request({ method: 'POST', path: '/x' }, thing));
    expect(error.serverMessage).toBeNull();
  });

  it('emits session-expired on 401, except for auth requests', async () => {
    const fetchImpl = vi.fn(async () => json({ detail: 'no' }, { status: 401 }));
    const c = client(fetchImpl);
    const listener = vi.fn();
    c.on('session-expired', listener);
    await rejection(c.request({ path: '/api/v1/auth/session', authRequest: true }, thing));
    expect(listener).not.toHaveBeenCalled();
    await rejection(c.request({ path: '/x' }, thing));
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('emits maintenance for 503 with the maintenance code and does not retry it', async () => {
    const fetchImpl = vi.fn(async () => json({ error: { code: 'maintenance', message: 'Down' } }, { status: 503 }));
    const c = client(fetchImpl);
    const listener = vi.fn();
    c.on('maintenance', listener);
    await rejection(c.request({ path: '/x' }, thing));
    expect(listener).toHaveBeenCalled();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('retries idempotent reads on 503 and then succeeds', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(json({ id: 'ok' }));
    expect(await client(fetchImpl).request({ path: '/x' }, thing)).toEqual({ id: 'ok' });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('never retries writes automatically', async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 503 }));
    const error = await rejection(client(fetchImpl).request({ method: 'POST', path: '/x' }, thing));
    expect(error.status).toBe(503);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('reports network failures and timeouts distinctly', async () => {
    const offline = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect((await rejection(client(offline, { maxReadRetries: 0 }).request({ path: '/x' }, thing))).kind).toBe('network');

    const slow = vi.fn(
      (_url: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const error = await rejection(client(slow as typeof fetch, { maxReadRetries: 0, writeTimeoutMs: 20 }).request({ method: 'POST', path: '/x' }, thing));
    expect(error.kind).toBe('timeout');
    expect(error.isWrite).toBe(true);
  });

  it('reports caller cancellation as aborted', async () => {
    const controller = new AbortController();
    const slow = vi.fn(
      (_url: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const promise = client(slow as typeof fetch).request({ path: '/x', signal: controller.signal }, thing);
    controller.abort();
    expect((await rejection(promise)).kind).toBe('aborted');
  });

  it('sends the CSRF token and idempotency key on writes only', async () => {
    document.cookie = 'cb_csrf=token-abc';
    const fetchImpl = vi.fn(async () => json({ id: 'a' }));
    const c = client(fetchImpl);
    await c.request({ method: 'POST', path: '/x', body: { a: 1 }, idempotencyKey: 'key-1' }, thing);
    await c.request({ path: '/x' }, thing);
    const writeHeaders = (fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Record<string, string>;
    const readHeaders = (fetchImpl.mock.calls[1] as unknown as [string, RequestInit])[1].headers as Record<string, string>;
    expect(writeHeaders['X-CSRF-Token']).toBe('token-abc');
    expect(writeHeaders['Idempotency-Key']).toBe('key-1');
    expect(writeHeaders['Content-Type']).toBe('application/json');
    expect(readHeaders['X-CSRF-Token']).toBeUndefined();
  });

  it('parses Retry-After for 429 responses', async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 429, headers: { 'retry-after': '120' } }));
    const error = await rejection(client(fetchImpl).request({ method: 'POST', path: '/x' }, thing));
    expect(error.retryAfterSeconds).toBe(120);
  });

  it('returns the status of body-less writes', async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 202 }));
    expect(await client(fetchImpl).send({ method: 'DELETE', path: '/x' })).toBe(202);
  });
});

describe('parseErrorBody', () => {
  it('maps FastAPI validation errors to dotted field paths', () => {
    const parsed = parseErrorBody({
      detail: [
        { loc: ['body', 'source', 'image'], msg: 'invalid image', type: 'value_error' },
        { loc: ['body', 'environment', 2, 'name'], msg: 'bad name', type: 'value_error' },
      ],
    });
    expect(parsed.fieldErrors).toEqual([
      { field: 'source.image', message: 'invalid image' },
      { field: 'environment.2.name', message: 'bad name' },
    ]);
  });

  it('ignores unexpected shapes', () => {
    expect(parseErrorBody('oops')).toEqual({ code: null, message: null, requestId: null, fieldErrors: [] });
    expect(parseErrorBody([1, 2])).toEqual({ code: null, message: null, requestId: null, fieldErrors: [] });
  });
});
