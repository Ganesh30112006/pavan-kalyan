/**
 * Tiny runtime decoders. They check that API responses match the TypeScript
 * types instead of trusting `as` casts, and ignore unknown extra fields so the
 * backend can add fields without breaking the client.
 */

export class DecodeError extends Error {
  constructor(
    readonly path: string,
    readonly expected: string,
  ) {
    super(`Expected ${expected} at ${path || '(root)'}`);
    this.name = 'DecodeError';
  }
}

export type Decoder<T> = (value: unknown, path: string) => T;

export function decode<T>(decoder: Decoder<T>, value: unknown): T {
  return decoder(value, '');
}

export const string: Decoder<string> = (value, path) => {
  if (typeof value !== 'string') throw new DecodeError(path, 'string');
  return value;
};

export const number: Decoder<number> = (value, path) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new DecodeError(path, 'number');
  return value;
};

export const integer: Decoder<number> = (value, path) => {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw new DecodeError(path, 'integer');
  return value;
};

export const boolean: Decoder<boolean> = (value, path) => {
  if (typeof value !== 'boolean') throw new DecodeError(path, 'boolean');
  return value;
};

/** An ISO-8601 timestamp string. */
export const dateTime: Decoder<string> = (value, path) => {
  const s = string(value, path);
  if (Number.isNaN(Date.parse(s))) throw new DecodeError(path, 'ISO date-time');
  return s;
};

export function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value, path) => {
    if (typeof value !== 'string' || !values.includes(value as T)) {
      throw new DecodeError(path, `one of ${values.join(', ')}`);
    }
    return value as T;
  };
}

/** Like oneOf, but an unrecognised string maps to `fallback` instead of failing. */
export function oneOfOr<T extends string>(values: readonly T[], fallback: T): Decoder<T> {
  return (value, path) => {
    const s = string(value, path);
    return values.includes(s as T) ? (s as T) : fallback;
  };
}

export function nullable<T>(decoder: Decoder<T>): Decoder<T | null> {
  return (value, path) => (value === null || value === undefined ? null : decoder(value, path));
}

export function optional<T>(decoder: Decoder<T>): Decoder<T | undefined> {
  return (value, path) => (value === undefined ? undefined : decoder(value, path));
}

export function withDefault<T>(decoder: Decoder<T>, fallback: T): Decoder<T> {
  return (value, path) => (value === undefined || value === null ? fallback : decoder(value, path));
}

export function array<T>(decoder: Decoder<T>): Decoder<T[]> {
  return (value, path) => {
    if (!Array.isArray(value)) throw new DecodeError(path, 'array');
    return value.map((item, index) => decoder(item, `${path}[${index}]`));
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function object<T>(shape: { [K in keyof T]-?: Decoder<T[K]> }): Decoder<T> {
  return (value, path) => {
    if (!isRecord(value)) throw new DecodeError(path, 'object');
    const result: Partial<T> = {};
    for (const key of Object.keys(shape) as Array<keyof T>) {
      const field = String(key);
      result[key] = shape[key](value[field], path ? `${path}.${field}` : field);
    }
    return result as T;
  };
}

export function record<K extends string, V>(keys: readonly K[], decoder: Decoder<V>): Decoder<Record<K, V>> {
  return (value, path) => {
    if (!isRecord(value)) throw new DecodeError(path, 'object');
    const result = {} as Record<K, V>;
    for (const key of keys) result[key] = decoder(value[key], `${path}.${key}`);
    return result;
  };
}

/** Null or an unrecognised value maps to null; a non-string still fails. */
export function oneOfOrNull<T extends string>(values: readonly T[]): Decoder<T | null> {
  return (value, path) => {
    if (value === null || value === undefined) return null;
    const s = string(value, path);
    return values.includes(s as T) ? (s as T) : null;
  };
}
