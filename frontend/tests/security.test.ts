import { describe, expect, it, vi } from 'vitest';
import { logger } from '../src/utils/logger';
import { redact, sanitizeServerMessage } from '../src/utils/redact';
import { safeExternalUrl, safeInternalPath, validateRepositoryUrl } from '../src/utils/url';
import { simulated, messages } from '../src/utils/messages';

describe('redact', () => {
  it.each([
    ['key AKIAABCDEFGHIJKLMNOP leaked', 'AKIAABCDEFGHIJKLMNOP'],
    ['Authorization: Bearer abc.def.ghi', 'abc.def.ghi'],
    ['password=hunter2&x=1', 'hunter2'],
    ['{"client_secret": "s3cr3t"}', 's3cr3t'],
    ['postgres://admin:pa55@db/x', 'pa55'],
    ['token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abcdefghijk', 'eyJhbGciOiJIUzI1NiJ9'],
    ['-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----', 'MIIE'],
  ])('removes secrets from %j', (input, secret) => {
    const out = redact(input);
    expect(out).not.toContain(secret);
    expect(out).toContain('[redacted]');
  });

  it('leaves ordinary text alone', () => {
    expect(redact('Deployment of orders-api failed at build')).toBe('Deployment of orders-api failed at build');
  });
});

describe('sanitizeServerMessage', () => {
  it.each([
    'Traceback (most recent call last): boom',
    'psycopg.errors.UniqueViolation: duplicate key',
    'SELECT * FROM users WHERE id = 1',
    'open /var/lib/cloudbridge/creds.json failed',
    'connect to 10.1.2.3 refused',
    'worker-3.internal timed out',
  ])('rejects internal detail: %s', (msg) => {
    expect(sanitizeServerMessage(msg)).toBeNull();
  });

  it('keeps and redacts user-facing messages, and truncates long ones', () => {
    expect(sanitizeServerMessage('Name already used')).toBe('Name already used');
    expect(sanitizeServerMessage('token=abc is invalid')).toBe('token=[redacted] is invalid');
    expect(sanitizeServerMessage('x'.repeat(500))!.length).toBeLessThanOrEqual(300);
    expect(sanitizeServerMessage(42)).toBeNull();
  });
});

describe('logger', () => {
  it('redacts secrets before writing to the console', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    logger.error('failed with password=hunter2', { header: 'Bearer abc123' });
    const written = spy.mock.calls.flat().join(' ');
    expect(written).not.toContain('hunter2');
    expect(written).not.toContain('abc123');
  });
});

describe('URL safety', () => {
  it('only links https URLs without credentials', () => {
    expect(safeExternalUrl('https://app.example.com/x')).toBe('https://app.example.com/x');
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
    expect(safeExternalUrl('data:text/html,hi')).toBeNull();
    expect(safeExternalUrl('http://app.example.com')).toBeNull();
    expect(safeExternalUrl('https://user:pw@app.example.com')).toBeNull();
    expect(safeExternalUrl('not a url')).toBeNull();
  });

  it('only redirects to internal paths after sign-in', () => {
    expect(safeInternalPath('/applications?q=x')).toBe('/applications?q=x');
    expect(safeInternalPath('//evil.example.com')).toBe('/');
    expect(safeInternalPath('https://evil.example.com')).toBe('/');
    expect(safeInternalPath('/\\evil.example.com')).toBe('/');
    expect(safeInternalPath('/logout')).toBe('/');
    expect(safeInternalPath(undefined)).toBe('/');
  });

  it('validates repository URLs', () => {
    expect(validateRepositoryUrl('https://github.com/org/repo.git')).toBeNull();
    expect(validateRepositoryUrl('http://github.com/org/repo.git')).toMatch(/https/);
    expect(validateRepositoryUrl('https://token@github.com/org/repo.git')).toMatch(/credentials/);
    expect(validateRepositoryUrl('https://169.254.169.254/latest')).toMatch(/IP address/);
    expect(validateRepositoryUrl('https://localhost/repo')).toMatch(/local host/);
    expect(validateRepositoryUrl('file:///etc/passwd')).toMatch(/https/);
  });
});

describe('demo messages', () => {
  it('marks every demo message as simulated', () => {
    expect(simulated(messages.deploymentSucceeded('api'), true).message).toMatch(/simulated result/i);
    expect(simulated(messages.deploymentSucceeded('api'), false).message).not.toMatch(/simulated/i);
  });
});
