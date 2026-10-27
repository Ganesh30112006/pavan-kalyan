import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import './feedback.css';

export type AlertTone = 'info' | 'success' | 'warning' | 'danger';

interface AlertProps {
  tone: AlertTone;
  title?: string;
  children?: ReactNode;
  actions?: ReactNode;
  requestId?: string | null;
  /** 'alert' interrupts screen readers; use only for errors that need attention now. */
  role?: 'alert' | 'status' | 'none';
  className?: string;
}

const ICONS = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle };

export function RequestId({ id }: { id: string | null | undefined }) {
  if (!id) return null;
  return (
    <span className="request-id">
      Request ID: <code>{id}</code>
    </span>
  );
}

export function Alert({ tone, title, children, actions, requestId, role = 'none', className }: AlertProps) {
  const Icon = ICONS[tone];
  return (
    <div className={`alert alert--${tone}${className ? ` ${className}` : ''}`} role={role === 'none' ? undefined : role}>
      <Icon className="alert__icon" size={18} aria-hidden="true" />
      <div className="alert__body">
        {title && <div className="alert__title">{title}</div>}
        {children && <div>{children}</div>}
        <RequestId id={requestId} />
        {actions && <div className="alert__actions">{actions}</div>}
      </div>
    </div>
  );
}
