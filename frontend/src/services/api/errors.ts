import type { ApiErrorKind, ErrorDescription, FieldError } from '../../types/errors';

export interface ApiErrorInit {
  kind: ApiErrorKind;
  status?: number | null;
  code?: string | null;
  /** Already sanitised; null when the server message was missing or unsafe. */
  serverMessage?: string | null;
  requestId?: string | null;
  fieldErrors?: FieldError[];
  retryAfterSeconds?: number | null;
  method?: string;
}

/** The single error type every API call rejects with. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly serverMessage: string | null;
  readonly requestId: string | null;
  readonly fieldErrors: FieldError[];
  readonly retryAfterSeconds: number | null;
  readonly method: string;

  constructor(init: ApiErrorInit) {
    super(init.serverMessage ?? `${init.kind}${init.status ? ` ${init.status}` : ''}`);
    this.name = 'ApiError';
    this.kind = init.kind;
    this.status = init.status ?? null;
    this.code = init.code ?? null;
    this.serverMessage = init.serverMessage ?? null;
    this.requestId = init.requestId ?? null;
    this.fieldErrors = init.fieldErrors ?? [];
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.method = (init.method ?? 'GET').toUpperCase();
  }

  get isWrite(): boolean {
    return this.method !== 'GET' && this.method !== 'HEAD';
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * The server's message for one field of a rejected submission (`field` or anything below it, such as
 * `events.0` for `events`), or null.
 */
export function fieldErrorFor(error: ApiError | null, field: string): string | null {
  const match = error?.fieldErrors.find((f) => f.field === field || f.field.startsWith(`${field}.`));
  return match?.message ?? null;
}

export function isAbort(error: unknown): boolean {
  return (
    (isApiError(error) && error.kind === 'aborted') ||
    (error instanceof DOMException && error.name === 'AbortError')
  );
}

/** Wraps anything thrown into an ApiError so callers handle one shape. */
export function toApiError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  if (error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError({ kind: 'aborted' });
  }
  return new ApiError({ kind: 'malformed' });
}

export function isMaintenance(error: unknown): boolean {
  return isApiError(error) && error.status === 503 && error.code === 'maintenance';
}

function retryAfterText(seconds: number | null): string {
  if (!seconds || seconds <= 0) return 'Wait a moment, then try again.';
  if (seconds < 90) return `Try again in ${seconds} seconds.`;
  return `Try again in about ${Math.ceil(seconds / 60)} minutes.`;
}

/**
 * Maps an error to user-facing text. Server text is used only for request
 * errors (400/409/422) where it explains what to change, and only after it
 * has been sanitised. Server errors never show backend text.
 */
export function describeError(error: unknown): ErrorDescription {
  const e = toApiError(error);

  switch (e.kind) {
    case 'aborted':
      return { title: 'Request cancelled', message: 'The request was cancelled.', recovery: 'retry' };
    case 'network':
      return typeof navigator !== 'undefined' && navigator.onLine === false
        ? {
            title: 'You are offline',
            message: 'Reconnect to the internet, then try again.',
            recovery: 'retry',
          }
        : {
            title: 'Unable to reach CloudBridge',
            message: 'The service could not be reached. Check your connection and try again.',
            recovery: e.isWrite ? 'check_status' : 'retry',
          };
    case 'timeout':
      return e.isWrite
        ? {
            title: 'The request timed out',
            message:
              'CloudBridge did not respond in time, so the outcome is unknown. Check the current status before trying again.',
            recovery: 'check_status',
          }
        : {
            title: 'The request timed out',
            message: 'CloudBridge did not respond in time. Try again.',
            recovery: 'retry',
          };
    case 'malformed':
      return {
        title: 'Unexpected response',
        message:
          'CloudBridge returned a response that could not be read. Try again, and contact support if it continues.',
        recovery: 'retry',
      };
    case 'http':
      break;
  }

  const status = e.status ?? 0;
  const detail = e.serverMessage;

  if (status === 400) {
    return { title: 'Invalid request', message: detail ?? 'The request could not be processed. Check the values and try again.', recovery: 'none' };
  }
  if (status === 401) {
    return { title: 'Your session has ended', message: 'Sign in again to continue.', recovery: 'sign_in' };
  }
  if (status === 403) {
    return {
      title: 'Access denied',
      message: 'You do not have permission to perform this action. Ask an organization administrator for access.',
      recovery: 'go_back',
    };
  }
  if (status === 404) {
    return { title: 'Not found', message: 'The requested resource does not exist or was removed.', recovery: 'go_back' };
  }
  if (status === 409) {
    return {
      title: 'Conflict',
      message: detail ?? 'The resource changed or the operation is already in progress. Reload to see the current state.',
      recovery: 'check_status',
    };
  }
  if (status === 422) {
    return { title: 'Check the highlighted fields', message: detail ?? 'Some values are not valid.', recovery: 'none' };
  }
  if (status === 429) {
    return { title: 'Too many requests', message: retryAfterText(e.retryAfterSeconds), recovery: 'retry' };
  }
  if (status === 503 && e.code === 'maintenance') {
    return {
      title: 'Scheduled maintenance',
      message: 'CloudBridge is temporarily unavailable for maintenance. Try again later.',
      recovery: 'retry',
    };
  }
  if (status === 502 || status === 503 || status === 504) {
    return {
      title: 'Service unavailable',
      message: e.isWrite
        ? 'CloudBridge or an upstream service is unavailable. The outcome of this request is unknown; check the current status before trying again.'
        : 'CloudBridge or an upstream service is temporarily unavailable. Try again shortly.',
      recovery: e.isWrite ? 'check_status' : 'retry',
    };
  }
  if (status >= 500) {
    return {
      title: 'Something went wrong on our side',
      message: e.isWrite
        ? 'The server reported an error. The operation may not have completed; check the current status before trying again.'
        : 'The server reported an error. Try again, and contact support if it continues.',
      recovery: e.isWrite ? 'check_status' : 'retry',
    };
  }
  return {
    title: 'Request could not be completed',
    message: detail ?? `The server responded with status ${status}.`,
    recovery: 'go_back',
  };
}
