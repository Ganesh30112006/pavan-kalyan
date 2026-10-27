import { describe, expect, it } from 'vitest';
import { ApiError, describeError, toApiError } from '../src/services/api/errors';

const http = (status: number, extra: Partial<ConstructorParameters<typeof ApiError>[0]> = {}) =>
  new ApiError({ kind: 'http', status, ...extra });

describe('describeError', () => {
  it.each([
    [400, 'Invalid request', 'none'],
    [401, 'Your session has ended', 'sign_in'],
    [403, 'Access denied', 'go_back'],
    [404, 'Not found', 'go_back'],
    [409, 'Conflict', 'check_status'],
    [422, 'Check the highlighted fields', 'none'],
    [429, 'Too many requests', 'retry'],
    [500, 'Something went wrong on our side', 'retry'],
    [502, 'Service unavailable', 'retry'],
    [503, 'Service unavailable', 'retry'],
    [504, 'Service unavailable', 'retry'],
    [418, 'Request could not be completed', 'go_back'],
  ])('maps HTTP %i to "%s"', (status, title, recovery) => {
    const d = describeError(http(status));
    expect(d.title).toBe(title);
    expect(d.recovery).toBe(recovery);
  });

  it('asks users to check status, not retry, when a write failed on the server', () => {
    expect(describeError(http(500, { method: 'POST' })).recovery).toBe('check_status');
    expect(describeError(http(504, { method: 'DELETE' })).message).toMatch(/outcome of this request is unknown/);
  });

  it('describes maintenance separately from other 503s', () => {
    expect(describeError(http(503, { code: 'maintenance' })).title).toBe('Scheduled maintenance');
  });

  it('never shows server text for 5xx errors', () => {
    expect(describeError(http(500, { serverMessage: 'db host 10.0.0.4 down' })).message).not.toMatch(/10\.0\.0\.4/);
  });

  it('uses sanitised server text for request errors', () => {
    expect(describeError(http(409, { serverMessage: 'A deployment is already running.' })).message).toBe('A deployment is already running.');
  });

  it('treats a timed-out write as an unknown outcome', () => {
    const d = describeError(new ApiError({ kind: 'timeout', method: 'POST' }));
    expect(d.recovery).toBe('check_status');
    expect(d.message).toMatch(/outcome is unknown/);
    expect(describeError(new ApiError({ kind: 'timeout' })).recovery).toBe('retry');
  });

  it('includes Retry-After guidance for 429', () => {
    expect(describeError(http(429, { retryAfterSeconds: 30 })).message).toBe('Try again in 30 seconds.');
    expect(describeError(http(429, { retryAfterSeconds: 600 })).message).toBe('Try again in about 10 minutes.');
  });

  it('wraps unknown thrown values', () => {
    expect(toApiError(new Error('x')).kind).toBe('malformed');
    expect(toApiError(new DOMException('x', 'AbortError')).kind).toBe('aborted');
  });
});
