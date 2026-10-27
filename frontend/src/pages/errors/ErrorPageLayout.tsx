import { Network, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { RequestId } from '../../components/feedback/Alert';
import { DemoBanner } from '../../components/layout/DemoBanner';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import '../../components/layout/layout.css';
import './errors.css';

interface ErrorPageLayoutProps {
  icon: LucideIcon;
  /** Short status line such as "Error 404". */
  code?: string;
  title: string;
  message: ReactNode;
  actions: ReactNode;
  requestId?: string | null;
  /** Outside the app shell (before sign-in, or when the shell itself cannot load). */
  standalone?: boolean;
}

export function ErrorPageLayout({ icon: Icon, code, title, message, actions, requestId, standalone = false }: ErrorPageLayoutProps) {
  useDocumentTitle(title);
  const content = (
    <section className={`error-page${standalone ? ' error-page--standalone' : ''}`} aria-labelledby="error-page-title">
      {code && (
        <span className="error-page__code">
          <Icon size={16} aria-hidden="true" />
          {code}
        </span>
      )}
      <h1 id="error-page-title">{title}</h1>
      <div className="error-page__message">{message}</div>
      <RequestId id={requestId} />
      <div className="error-page__actions">{actions}</div>
    </section>
  );
  if (!standalone) return content;
  return (
    <>
      <DemoBanner />
      <main className="centered-page">
        <div className="centered-page__inner">
          <div className="standalone-brand">
            <span className="app-header__logo" aria-hidden="true">
              <Network size={16} />
            </span>
            CloudBridge
          </div>
          {content}
        </div>
      </main>
    </>
  );
}
