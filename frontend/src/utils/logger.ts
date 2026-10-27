/* eslint-disable no-console -- this module is the only place allowed to write to the console. */
import { redact } from './redact';

type Context = Record<string, string | number | boolean | null | undefined>;

function format(message: string, context?: Context): string {
  if (!context) return redact(message);
  const parts = Object.entries(context)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}=${String(value)}`);
  return redact(`${message} ${parts.join(' ')}`);
}

/**
 * Minimal logger. Accepts only flat, primitive context so request bodies,
 * headers and response payloads cannot be logged by accident, and redacts
 * anything credential-shaped before writing.
 */
export const logger = {
  warn(message: string, context?: Context): void {
    console.warn(`[cloudbridge] ${format(message, context)}`);
  },
  error(message: string, context?: Context): void {
    console.error(`[cloudbridge] ${format(message, context)}`);
  },
};
