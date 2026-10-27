import { ExternalLink as ExternalIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { safeExternalUrl } from '../../utils/url';

interface ExternalLinkProps {
  href: string | null | undefined;
  children?: ReactNode;
}

/**
 * Links to an external destination only after validating the URL (https,
 * no credentials). Anything else is shown as plain text, never as a link.
 */
export function ExternalLink({ href, children }: ExternalLinkProps) {
  const safe = safeExternalUrl(href);
  if (!safe) return <span className="mono">{children ?? href ?? '—'}</span>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
      {children ?? safe}
      <ExternalIcon size={12} aria-hidden="true" style={{ marginLeft: 4, verticalAlign: '-1px' }} />
      <span className="visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}
