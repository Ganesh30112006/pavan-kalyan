import { KeyRound, Plus, ShieldAlert, Trash2 } from 'lucide-react';
import { emptyEnvRow, emptySecretRow, type EnvRow, type SecretRow } from '../../utils/environment';
import { looksSecret } from '../../utils/validation';
import { Alert } from '../feedback/Alert';
import { Button } from './Button';
import { TextField } from './Field';
import './forms.css';

interface EditorProps<R> {
  rows: R[];
  onChange(rows: R[]): void;
  errorFor(key: string): string | null;
  disabled?: boolean;
  readOnly?: boolean;
}

export function EnvironmentVariablesEditor({ rows, onChange, errorFor, disabled = false, readOnly = false }: EditorProps<EnvRow>) {
  const update = (index: number, patch: Partial<EnvRow>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  return (
    <div className="stack">
      <Alert tone="warning" title="Do not put secrets in environment variables">
        Environment variable values are stored and displayed in plain text to anyone who can view this application. Put
        passwords, tokens and keys in your cloud secret manager and add them below as secret references.
      </Alert>
      {rows.length === 0 ? (
        <p className="text-muted">No environment variables.</p>
      ) : (
        <div className="env-table">
          {rows.map((row, i) => (
            <div className="env-row" key={row.key}>
              <TextField
                label={`Variable ${i + 1} name`}
                mono
                spellCheck={false}
                autoComplete="off"
                value={row.name}
                onChange={(e) => update(i, { name: e.target.value })}
                error={errorFor(`environment.${i}.name`)}
                hint={
                  looksSecret(row.name) ? (
                    <span className="field__warning">
                      <ShieldAlert size={14} aria-hidden="true" /> This looks like a secret. Use a secret reference instead.
                    </span>
                  ) : undefined
                }
                disabled={disabled}
                readOnly={readOnly}
              />
              <TextField
                label={`Variable ${i + 1} value`}
                mono
                spellCheck={false}
                autoComplete="off"
                value={row.value}
                onChange={(e) => update(i, { value: e.target.value })}
                error={errorFor(`environment.${i}.value`)}
                disabled={disabled}
                readOnly={readOnly}
              />
              {!readOnly && (
                <Button
                  className="env-row__remove"
                  variant="ghost"
                  iconOnly
                  aria-label={`Remove variable ${row.name || i + 1}`}
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}
                  disabled={disabled}
                >
                  <Trash2 size={16} aria-hidden="true" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {!readOnly && (
        <div>
          <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => onChange([...rows, emptyEnvRow()])} disabled={disabled}>
            Add variable
          </Button>
        </div>
      )}
    </div>
  );
}

export function SecretReferencesEditor({ rows, onChange, errorFor, disabled = false, readOnly = false }: EditorProps<SecretRow>) {
  const update = (index: number, patch: Partial<SecretRow>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  return (
    <div className="stack">
      <p className="text-muted text-small">
        Each reference points to a secret in AWS Secrets Manager, AWS Systems Manager Parameter Store or Google Secret
        Manager. The provider injects the value at runtime; CloudBridge stores only the reference and never reads or
        shows the value.
      </p>
      {rows.length === 0 ? (
        <p className="text-muted">
          <KeyRound size={14} aria-hidden="true" /> No secret references.
        </p>
      ) : (
        <div className="env-table">
          {rows.map((row, i) => (
            <div className="env-row" key={row.key}>
              <TextField
                label={`Secret ${i + 1} variable name`}
                mono
                spellCheck={false}
                autoComplete="off"
                value={row.name}
                onChange={(e) => update(i, { name: e.target.value })}
                error={errorFor(`secret_refs.${i}.name`)}
                disabled={disabled}
                readOnly={readOnly}
              />
              <TextField
                label={`Secret ${i + 1} reference`}
                mono
                spellCheck={false}
                autoComplete="off"
                placeholder="arn:aws:secretsmanager:… or projects/…/secrets/…"
                value={row.reference}
                onChange={(e) => update(i, { reference: e.target.value })}
                error={errorFor(`secret_refs.${i}.reference`)}
                disabled={disabled}
                readOnly={readOnly}
              />
              {!readOnly && (
                <Button
                  className="env-row__remove"
                  variant="ghost"
                  iconOnly
                  aria-label={`Remove secret ${row.name || i + 1}`}
                  onClick={() => onChange(rows.filter((_, j) => j !== i))}
                  disabled={disabled}
                >
                  <Trash2 size={16} aria-hidden="true" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {!readOnly && (
        <div>
          <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => onChange([...rows, emptySecretRow()])} disabled={disabled}>
            Add secret reference
          </Button>
        </div>
      )}
    </div>
  );
}
