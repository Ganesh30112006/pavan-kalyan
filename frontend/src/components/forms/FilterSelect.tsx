import { useId, type ReactNode } from 'react';
import './forms.css';

interface FilterSelectProps {
  label: string;
  value: string;
  onChange(value: string): void;
  children: ReactNode;
  /** Show the label above the control instead of only to assistive technology. */
  visibleLabel?: boolean;
}

/** Compact select for list toolbars. Always has an accessible label. */
export function FilterSelect({ label, value, onChange, children, visibleLabel = false }: FilterSelectProps) {
  const id = useId();
  return (
    <div className={visibleLabel ? 'field' : 'filter-select'}>
      <label className={visibleLabel ? 'field__label text-small' : 'visually-hidden'} htmlFor={id}>
        {label}
      </label>
      <select id={id} className="select select--auto" value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </div>
  );
}
