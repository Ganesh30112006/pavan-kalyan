import { Check, Copy } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from './Button';

/** Copies a non-secret value (IDs, policy text). Never used for secret values. */
export function CopyButton({ value, label, showLabel = false }: { value: string; label: string; showLabel?: boolean }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');

  useEffect(() => {
    if (state === 'idle') return undefined;
    const timer = setTimeout(() => setState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [state]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState('copied');
    } catch {
      setState('failed');
    }
  };

  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        iconOnly={!showLabel}
        aria-label={showLabel ? undefined : `Copy ${label}`}
        onClick={copy}
        icon={state === 'copied' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      >
        {showLabel ? `Copy ${label}` : null}
      </Button>
      <span className="visually-hidden" role="status">
        {state === 'copied' ? `${label} copied` : state === 'failed' ? `Could not copy ${label}` : ''}
      </span>
      {state === 'failed' && <span className="text-small text-muted">Copy failed; select the text instead.</span>}
    </>
  );
}
