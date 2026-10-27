import type { EnvironmentVariable, SecretReference } from '../types/api';
import { randomId } from './id';
import { envVarName, secretReference } from './validation';

/** Editable rows carry a stable key so React keeps focus while rows are added and removed. */
export interface EnvRow {
  key: string;
  name: string;
  value: string;
}

export interface SecretRow {
  key: string;
  name: string;
  reference: string;
}

export const toEnvRows = (vars: EnvironmentVariable[]): EnvRow[] => vars.map((v) => ({ key: randomId(), ...v }));
export const toSecretRows = (refs: SecretReference[]): SecretRow[] => refs.map((r) => ({ key: randomId(), ...r }));
export const fromEnvRows = (rows: EnvRow[]): EnvironmentVariable[] => rows.map(({ name, value }) => ({ name: name.trim(), value }));
export const fromSecretRows = (rows: SecretRow[]): SecretReference[] =>
  rows.map(({ name, reference }) => ({ name: name.trim(), reference: reference.trim() }));

export const emptyEnvRow = (): EnvRow => ({ key: randomId(), name: '', value: '' });
export const emptySecretRow = (): SecretRow => ({ key: randomId(), name: '', reference: '' });

/**
 * Errors keyed like the backend's field paths (environment.0.name, secret_refs.1.reference),
 * so server-side 422 errors land on the same rows.
 */
export function validateRows(env: EnvRow[], secrets: SecretRow[]): Record<string, string> {
  const errors: Record<string, string> = {};
  const seen = new Set<string>();
  env.forEach((row, i) => {
    const name = row.name.trim();
    const nameError = envVarName(name);
    if (nameError) errors[`environment.${i}.name`] = `Variable ${i + 1}: ${nameError}`;
    else if (seen.has(name)) errors[`environment.${i}.name`] = `Variable ${i + 1}: ${name} is defined more than once.`;
    seen.add(name);
    if (row.value.length > 4096) errors[`environment.${i}.value`] = `Variable ${i + 1}: values are limited to 4096 characters.`;
  });
  secrets.forEach((row, i) => {
    const name = row.name.trim();
    const nameError = envVarName(name);
    if (nameError) errors[`secret_refs.${i}.name`] = `Secret ${i + 1}: ${nameError}`;
    else if (seen.has(name)) errors[`secret_refs.${i}.name`] = `Secret ${i + 1}: ${name} is already used by another variable or secret.`;
    seen.add(name);
    const refError = secretReference(row.reference);
    if (refError) errors[`secret_refs.${i}.reference`] = `Secret ${i + 1}: ${refError}`;
  });
  return errors;
}
