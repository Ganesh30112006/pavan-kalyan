import { describe, expect, it } from 'vitest';
import { decode, DecodeError } from '../src/services/api/decode';
import { applicationSummary, deployment, session } from '../src/services/api/decoders';
import { makeAppSummary, makeDeployment, makeSession } from './helpers/fixtures';

describe('decoders', () => {
  it('accepts valid data and ignores extra fields', () => {
    const app = decode(applicationSummary, { ...makeAppSummary(), future_field: true });
    expect(app.name).toBe('orders-api');
    expect('future_field' in app).toBe(false);
  });

  it('maps unknown status values to "unknown" instead of failing', () => {
    expect(decode(applicationSummary, { ...makeAppSummary(), status: 'hibernating' }).status).toBe('unknown');
  });

  it('fails with the path of a missing required field', () => {
    const rest: Record<string, unknown> = { ...makeAppSummary() };
    delete rest.name;
    expect(() => decode(applicationSummary, rest)).toThrow(DecodeError);
    expect(() => decode(applicationSummary, rest)).toThrow(/name/);
  });

  it('never grants permissions it does not recognise', () => {
    const s = decode(session, { ...makeSession(['audit:read']), permissions: ['audit:read', 'everything:*'] });
    expect(s.permissions).toEqual(['audit:read']);
  });

  it('ignores progress values outside 0–100 rather than showing invented progress', () => {
    expect(decode(deployment, { ...makeDeployment(), progress_percent: 140 }).progress_percent).toBeNull();
    expect(decode(deployment, { ...makeDeployment(), progress_percent: 40 }).progress_percent).toBe(40);
  });

  it('rejects invalid timestamps', () => {
    expect(() => decode(applicationSummary, { ...makeAppSummary(), updated_at: 'yesterday' })).toThrow(DecodeError);
  });
});
