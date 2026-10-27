import { useId, useState, type ReactNode } from 'react';
import type { ApiError } from '../../services/api/errors';
import { FormErrorSummary } from '../forms/FormErrorSummary';
import { Button } from '../forms/Button';
import { Dialog } from './Dialog';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** What will happen, in plain words. */
  description: ReactNode;
  /** What the action affects (resource names, provider, region). */
  affected?: ReactNode;
  confirmLabel: string;
  pendingLabel: string;
  tone?: 'danger' | 'primary';
  /** Requires typing this exact text before confirming (for irreversible actions). */
  confirmText?: string;
  pending: boolean;
  error: ApiError | null;
  onConfirm(): void;
  onCancel(): void;
  /** Shown when the outcome of a failed request is uncertain. */
  onCheckStatus?: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  affected,
  confirmLabel,
  pendingLabel,
  tone = 'danger',
  confirmText,
  pending,
  error,
  onConfirm,
  onCancel,
  onCheckStatus,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('');
  const inputId = useId();
  const confirmed = !confirmText || typed === confirmText;

  const close = () => {
    setTyped('');
    onCancel();
  };

  return (
    <Dialog
      open={open}
      title={title}
      onClose={close}
      busy={pending}
      role="alertdialog"
      footer={
        <>
          <Button onClick={close} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant={tone}
            onClick={onConfirm}
            disabled={!confirmed}
            loading={pending}
            loadingLabel={pendingLabel}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div>{description}</div>
      {affected && <div className="dialog__affected">{affected}</div>}
      {confirmText && (
        <div className="field">
          <label className="field__label" htmlFor={inputId}>
            Type <code>{confirmText}</code> to confirm
          </label>
          <input
            id={inputId}
            className="input input--mono"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setTyped(e.target.value)}
            disabled={pending}
          />
        </div>
      )}
      <FormErrorSummary error={error} onRetry={onConfirm} onCheckStatus={onCheckStatus} />
    </Dialog>
  );
}
