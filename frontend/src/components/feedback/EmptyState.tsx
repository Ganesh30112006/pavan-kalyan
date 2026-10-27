import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import './feedback.css';

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  message: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ icon: Icon, title, message, actions, compact = false }: EmptyStateProps) {
  return (
    <div className={`state-block${compact ? ' state-block--compact' : ''}`}>
      <Icon className="state-block__icon" size={compact ? 24 : 32} aria-hidden="true" />
      <h2>{title}</h2>
      <p className="state-block__message">{message}</p>
      {actions && <div className="state-block__actions">{actions}</div>}
    </div>
  );
}
