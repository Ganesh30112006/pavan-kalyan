import { describe, expect, it } from 'vitest';
import { decode, DecodeError } from '../src/services/api/decode';
import { applicationSummary, connectionSetup, deployment, session } from '../src/services/api/decoders';
import { makeAppSummary, makeConnection, makeDeployment, makeSession } from './helpers/fixtures';

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

  it('reads the Launch Stack template URL, and treats an older response without it as none', () => {
    const older: Record<string, unknown> = { ...makeConnection().setup! };
    delete older.cloudformation_template_url;
    expect(decode(connectionSetup, older).cloudformation_template_url).toBeNull();
    const url = 'https://cb.s3.amazonaws.com/t.json';
    expect(decode(connectionSetup, { ...older, cloudformation_template_url: url }).cloudformation_template_url).toBe(url);
    expect(() => decode(connectionSetup, { ...older, cloudformation_template_url: 42 })).toThrow(/cloudformation_template_url/);
  });

  it('rejects invalid timestamps', () => {
    expect(() => decode(applicationSummary, { ...makeAppSummary(), updated_at: 'yesterday' })).toThrow(DecodeError);
  });
});
