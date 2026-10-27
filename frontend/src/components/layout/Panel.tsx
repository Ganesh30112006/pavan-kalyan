import type { ReactNode } from 'react';

interface PanelProps {
  title?: string;
  /** Heading level for the title, to keep the outline correct. */
  headingLevel?: 2 | 3;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Remove body padding (for tables that run edge to edge). */
  flush?: boolean;
  className?: string;
  id?: string;
}

export function Panel({ title, headingLevel = 2, actions, children, footer, flush = false, className, id }: PanelProps) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`panel${className ? ` ${className}` : ''}`} id={id} aria-labelledby={headingId}>
      {(title || actions) && (
        <div className="panel__header">
          {title && <Heading id={headingId}>{title}</Heading>}
          {actions && <div className="cluster">{actions}</div>}
        </div>
      )}
      {flush ? children : <div className="panel__body">{children}</div>}
      {footer && <div className="panel__footer">{footer}</div>}
    </section>
  );
}
