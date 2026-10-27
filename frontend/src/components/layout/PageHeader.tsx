import type { ReactNode } from 'react';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { Breadcrumbs, type Crumb } from '../navigation/Breadcrumbs';
import './layout.css';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  breadcrumbs?: Crumb[];
  /** Status badge or similar, shown next to the title. */
  meta?: ReactNode;
  actions?: ReactNode;
  /** Document title, when it should differ from the heading. */
  documentTitle?: string;
}

export function PageHeader({ title, description, breadcrumbs = [], meta, actions, documentTitle }: PageHeaderProps) {
  useDocumentTitle(documentTitle ?? title);
  return (
    <header className="page-header">
      <Breadcrumbs items={breadcrumbs} />
      <div className="page-header__row">
        <div className="page-header__title">
          <h1>{title}</h1>
          {meta}
        </div>
        {actions && <div className="page-header__actions">{actions}</div>}
      </div>
      {description && <p className="page-header__description">{description}</p>}
    </header>
  );
}
