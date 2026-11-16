import { Download } from 'lucide-react';
import type { ReactNode } from 'react';
import { CopyButton } from '../../components/forms/CopyButton';
import { SETUP_SCRIPT, downloadAndRun, scriptUrl } from './setupCommand';

const SHELLS = {
  aws: { label: 'AWS CloudShell', href: 'https://console.aws.amazon.com/cloudshell/home', cli: 'the AWS CLI' },
  azure: { label: 'Azure Cloud Shell', href: 'https://shell.azure.com', cli: 'the Azure CLI' },
  kubernetes: { label: null, href: null, cli: 'kubectl pointed at the cluster' },
} as const;

/**
 * How to run the guided setup script: where, and one command that downloads and starts it. The script runs with
 * the customer's own sign-in and asks before changing anything; CloudBridge never sees their credentials.
 */
export function ScriptSetup({ shell, command, where, after }: { shell: keyof typeof SHELLS; command: string; where?: ReactNode; after: ReactNode }) {
  const { label, href, cli } = SHELLS[shell];
  const download = scriptUrl('', import.meta.env.BASE_URL);
  const oneStep = downloadAndRun(scriptUrl(window.location.origin, import.meta.env.BASE_URL), command);
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
        Paste and run this. It downloads the script and starts it:
        <pre className="code-block code-block--wrap">{oneStep}</pre>
        <CopyButton value={oneStep} label="setup command" showLabel />
        <p className="field__hint">
          It lists every change it will make, then asks once (answer <code>s</code> to approve step by step). Add{' '}
          <code>--dry-run</code> to only see the steps. It never creates access keys or secrets.
        </p>
        <details className="text-small">
          <summary>Can’t download from the shell?</summary>
          <p>
            <a href={download} download={SETUP_SCRIPT}>
              <Download size={14} aria-hidden="true" className="icon-inline" /> Download {SETUP_SCRIPT}
            </a>
            , upload it to the shell, then run:
          </p>
          <pre className="code-block code-block--wrap">{command}</pre>
          <CopyButton value={command} label="run command" showLabel />
        </details>
      </li>
      <li>{after}</li>
    </ol>
  );
}
