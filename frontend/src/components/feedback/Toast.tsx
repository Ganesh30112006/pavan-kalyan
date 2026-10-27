import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import type { ToastInput } from '../../app/contexts';
import { Button } from '../forms/Button';
import { RequestId } from './Alert';
import './feedback.css';

export interface ToastItem extends ToastInput {
  id: string;
}

const ICONS = { success: CheckCircle2, info: Info, warning: AlertTriangle, error: XCircle };

interface ToastProps {
  toast: ToastItem;
  demo: boolean;
  onDismiss(id: string): void;
  onPause(paused: boolean): void;
}

export function Toast({ toast, demo, onDismiss, onPause }: ToastProps) {
  const Icon = ICONS[toast.tone];
  return (
    <div
      className={`toast toast--${toast.tone}`}
      role={toast.tone === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => onPause(true)}
      onMouseLeave={() => onPause(false)}
      onFocus={() => onPause(true)}
      onBlur={() => onPause(false)}
    >
      <Icon className="toast__icon" size={18} aria-hidden="true" />
      <div className="toast__body">
        <div className="toast__title">
          {demo && <span className="demo-tag">Demo</span>} {toast.title}
        </div>
        {toast.message && <div className="toast__message">{toast.message}</div>}
        <RequestId id={toast.requestId} />
      </div>
      <Button className="toast__close" variant="ghost" size="sm" iconOnly aria-label="Dismiss notification" onClick={() => onDismiss(toast.id)}>
        <X size={14} aria-hidden="true" />
      </Button>
    </div>
  );
}
