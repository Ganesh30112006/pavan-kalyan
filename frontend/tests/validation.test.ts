import { describe, expect, it } from 'vitest';
import {
  applicationName,
  awsAccountId,
  awsRoleArn,
  containerImage,
  envVarName,
  gcpProjectId,
  gcpServiceAccount,
  gcpWorkloadIdentityProvider,
  looksSecret,
} from '../src/utils/validation';

describe('validators', () => {
  it('validates application names', () => {
    expect(applicationName('orders-api')).toBeNull();
    expect(applicationName('')).toMatch(/required/);
    expect(applicationName('ab')).toMatch(/3 to 40/);
    expect(applicationName('Orders')).toMatch(/lowercase/);
    expect(applicationName('a--b')).toMatch(/consecutive/);
  });

  it('validates image references', () => {
    expect(containerImage('123456789012.dkr.ecr.us-east-1.amazonaws.com/app:1.4.0')).toBeNull();
    expect(containerImage('us-docker.pkg.dev/proj/repo/app@sha256:' + 'a'.repeat(64))).toBeNull();
    expect(containerImage('https://registry/app')).toMatch(/scheme/);
    expect(containerImage('app')).toMatch(/registry/);
  });

  it('validates provider identifiers', () => {
    expect(awsAccountId('123456789012')).toBeNull();
    expect(awsAccountId('1234')).not.toBeNull();
    expect(awsRoleArn('arn:aws:iam::123456789012:role/Deploy')).toBeNull();
    expect(awsRoleArn('arn:aws:iam::123:role/Deploy')).not.toBeNull();
    expect(gcpProjectId('my-project-1')).toBeNull();
    expect(gcpProjectId('1project')).not.toBeNull();
    expect(gcpServiceAccount('cloudbridge@my-project-1.iam.gserviceaccount.com')).toBeNull();
    expect(gcpServiceAccount('cb@my-project-1.iam.gserviceaccount.com')).not.toBeNull(); // IDs are 6–30 characters
    expect(gcpWorkloadIdentityProvider('projects/123/locations/global/workloadIdentityPools/pool1/providers/prov1')).toBeNull();
    expect(gcpWorkloadIdentityProvider('pool1')).not.toBeNull();
  });

  it('flags variable names that look like secrets', () => {
    expect(envVarName('LOG_LEVEL')).toBeNull();
    expect(envVarName('1BAD')).not.toBeNull();
    expect(looksSecret('STRIPE_API_KEY')).toBe(true);
    expect(looksSecret('LOG_LEVEL')).toBe(false);
  });
});
