import { createContext } from 'react';
import type { CloudBridgeApi } from '../services/api';
import type { ApiError } from '../services/api/errors';
import type { LoginRequest, Permission, Session, User } from '../types/api';
import type { MutationResult } from '../hooks/useMutation';

export const ApiContext = createContext<CloudBridgeApi | null>(null);

export type AuthStatus =
  /** Checking for an existing session. */
  | 'loading'
  | 'authenticated'
  /** No session. */
  | 'anonymous'
  /** The session ended while the app was in use (a 401 from the API). */
  | 'expired'
  /** The session check failed because the backend could not be reached. */
  | 'unavailable'
  | 'maintenance';

export interface AuthContextValue {
  status: AuthStatus;
  session: Session | null;
  /** Why the session check failed, when status is 'unavailable'. */
  bootstrapError: ApiError | null;
  login(input: LoginRequest): Promise<MutationResult<Session>>;
  logout(): Promise<MutationResult<void>>;
  retry(): void;
  /** Display logic only: the backend enforces every permission independently. */
  can(permission: Permission): boolean;
  updateUser(user: User): void;
  updateOrganizationName(name: string): void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export type ToastTone = 'success' | 'info' | 'warning' | 'error';

export interface ToastInput {
  tone: ToastTone;
  title: string;
  message?: string;
  requestId?: string | null;
  /** Errors stay until dismissed; others auto-dismiss unless this is true. */
  persistent?: boolean;
}

export interface ToastContextValue {
  notify(toast: ToastInput): string;
  dismiss(id: string): void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export interface AnnouncerContextValue {
  /** Sends a message to the polite screen-reader live region. */
  announce(message: string): void;
}

export const AnnouncerContext = createContext<AnnouncerContextValue | null>(null);
