export type ApiErrorKind =
  /** The server answered with a non-2xx status. */
  | 'http'
  /** The request never reached the server, or the connection dropped. */
  | 'network'
  /** No response within the time limit. The outcome of a write is unknown. */
  | 'timeout'
  /** Cancelled by the app (navigation, unmount, a newer request). */
  | 'aborted'
  /** A 2xx response whose body was not what the API contract promises. */
  | 'malformed';

export interface FieldError {
  /** Dotted path into the request body, e.g. "environment.2.name". */
  field: string;
  message: string;
}

export type RecoveryAction = 'retry' | 'sign_in' | 'go_back' | 'go_home' | 'check_status' | 'none';

export interface ErrorDescription {
  title: string;
  message: string;
  recovery: RecoveryAction;
}
