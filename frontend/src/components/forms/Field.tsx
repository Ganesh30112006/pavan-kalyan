import { AlertCircle } from 'lucide-react';
import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import './forms.css';

interface FieldShellProps {
  label: string;
  required?: boolean;
  /** Shows "(optional)" next to the label. */
  optional?: boolean;
  hint?: ReactNode;
  error?: string | null;
  className?: string;
  children: (ids: { inputId: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
}

export function FieldShell({ label, required, optional, hint, error, className, children }: FieldShellProps) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined;
  return (
    <div className={`field${className ? ` ${className}` : ''}`}>
      <label className="field__label" htmlFor={inputId}>
        {label}
        {required && (
          <span className="field__indicator field__indicator--required" aria-hidden="true">
            *
          </span>
        )}
        {optional && <span className="field__indicator">(optional)</span>}
      </label>
      {children({ inputId, describedBy, invalid: Boolean(error) })}
      {error && (
        <span className="field__error" id={errorId}>
          <AlertCircle size={14} aria-hidden="true" />
          {error}
        </span>
      )}
      {hint && (
        <span className="field__hint" id={hintId}>
          {hint}
        </span>
      )}
    </div>
  );
}

type Common = Omit<FieldShellProps, 'children'> & { mono?: boolean };

type TextFieldProps = Common & Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'children'>;

export function TextField({ label, required, optional, hint, error, className, mono, ...input }: TextFieldProps) {
  return (
    <FieldShell label={label} required={required} optional={optional} hint={hint} error={error} className={className}>
      {({ inputId, describedBy, invalid }) => (
        <input
          id={inputId}
          className={`input${mono ? ' input--mono' : ''}`}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...input}
        />
      )}
    </FieldShell>
  );
}

type TextAreaFieldProps = Common & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id' | 'children'>;

export function TextAreaField({ label, required, optional, hint, error, className, mono, ...input }: TextAreaFieldProps) {
  return (
    <FieldShell label={label} required={required} optional={optional} hint={hint} error={error} className={className}>
      {({ inputId, describedBy, invalid }) => (
        <textarea
          id={inputId}
          className={`textarea${mono ? ' input--mono' : ''}`}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...input}
        />
      )}
    </FieldShell>
  );
}

type SelectFieldProps = Common &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> & { children: ReactNode };

export function SelectField({ label, required, optional, hint, error, className, children, mono: _mono, ...select }: SelectFieldProps) {
  return (
    <FieldShell label={label} required={required} optional={optional} hint={hint} error={error} className={className}>
      {({ inputId, describedBy, invalid }) => (
        <select
          id={inputId}
          className="select"
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          required={required}
          {...select}
        >
          {children}
        </select>
      )}
    </FieldShell>
  );
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> {
  label: string;
  description?: string;
}

export function Checkbox({ label, description, ...input }: CheckboxProps) {
  const id = useId();
  return (
    <div className="checkbox">
      <input id={id} type="checkbox" aria-describedby={description ? `${id}-desc` : undefined} {...input} />
      <span className="checkbox__text">
        <label htmlFor={id}>{label}</label>
        {description && (
          <span id={`${id}-desc`} className="field__hint">
            {description}
          </span>
        )}
      </span>
    </div>
  );
}
