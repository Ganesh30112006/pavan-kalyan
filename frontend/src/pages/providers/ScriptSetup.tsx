import { Download } from 'lucide-react';
import type { ReactNode } from 'react';
import { CopyButton } from '../../components/forms/CopyButton';
import { SETUP_SCRIPT, scriptUrl } from './setupCommand';

const SHELLS = {
  aws: { label: 'AWS CloudShell', href: 'https://console.aws.amazon.com/cloudshell/home', cli: 'the AWS CLI' },
  azure: { label: 'Azure Cloud Shell', href: 'https://shell.azure.com', cli: 'the Azure CLI' },
  kubernetes: { label: null, href: null, cli: 'kubectl pointed at the cluster' },
} as const;

/**
 * How to run the guided setup script: where, how to get it, and the exact command. The script runs with the
 * customer's own sign-in and asks before every change; CloudBridge never sees their credentials.
 */
export function ScriptSetup({ shell, command, where, after }: { shell: keyof typeof SHELLS; command: string; where?: ReactNode; after: ReactNode }) {
  const { label, href, cli } = SHELLS[shell];
  const download = scriptUrl('', import.meta.env.BASE_URL);
  const fetchCommand = `curl -fsSLO ${scriptUrl(window.location.origin, import.meta.env.BASE_URL)}`;
  return (
    <ol className="setup-steps">
      <li>
        {href ? (
          <>
            Open{' '}
            <a href={href} target="_blank" rel="noreferrer">
              {label}
            </a>
            {where}, or any terminal where {cli} is signed in.
          </>
        ) : (
          <>Open a terminal with {cli}.</>
        )}
      </li>
      <li>
        Get the script:{' '}
        <a href={download} download={SETUP_SCRIPT}>
          <Download size={14} aria-hidden="true" className="icon-inline" /> Download {SETUP_SCRIPT}
        </a>{' '}
        and upload it, or run:
        <pre className="code-block code-block--wrap">{fetchCommand}</pre>
        <CopyButton value={fetchCommand} label="download command" showLabel />
      </li>
      <li>
        Run:
        <pre className="code-block code-block--wrap">{command}</pre>
        <CopyButton value={command} label="setup command" showLabel />
        <p className="field__hint">
          It shows every change and asks before making it; you can skip any step. Add <code>--dry-run</code> to only see
          the steps. It never creates access keys or secrets.
        </p>
      </li>
      <li>{after}</li>
    </ol>
  );
}
