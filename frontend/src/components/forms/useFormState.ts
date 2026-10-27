import { useCallback, useMemo, useState } from 'react';
import type { ApiError } from '../../services/api/errors';
import type { Validator } from '../../utils/validation';

export type Values = Record<string, string>;

/**
 * Minimal form state: values, which fields were touched, client-side
 * validation, and mapping of server field errors (422) back onto fields.
 */
export function useFormState<V extends Values>(initial: V, validators: Partial<Record<keyof V, Validator>>) {
  const [values, setValues] = useState<V>(initial);
  const [baseline, setBaseline] = useState<V>(initial);
  const [touched, setTouched] = useState<Partial<Record<keyof V, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Partial<Record<keyof V, string>>>({});

  const clientErrors = useMemo(() => {
    const result: Partial<Record<keyof V, string>> = {};
    for (const key of Object.keys(validators) as Array<keyof V>) {
      const message = validators[key]?.(values[key] ?? '');
      if (message) result[key] = message;
    }
    return result;
  }, [values, validators]);

  const isValid = Object.keys(clientErrors).length === 0;
  const isDirty = useMemo(
    () => (Object.keys(values) as Array<keyof V>).some((k) => values[k] !== baseline[k]),
    [values, baseline],
  );

  const setValue = useCallback((key: keyof V, value: string) => {
    setValues((v) => ({ ...v, [key]: value }));
    setServerErrors((e) => {
      if (!(key in e)) return e;
      const next = { ...e };
      delete next[key];
      return next;
    });
  }, []);

  const touch = useCallback((key: keyof V) => setTouched((t) => ({ ...t, [key]: true })), []);

  /** The message to show for a field: server errors always, client errors once touched or submitted. */
  const errorFor = useCallback(
    (key: keyof V): string | null =>
      serverErrors[key] ?? ((touched[key] || submitted) && clientErrors[key] ? (clientErrors[key] ?? null) : null),
    [serverErrors, touched, submitted, clientErrors],
  );

  /** Maps a server validation error onto fields. Returns messages that matched no field. */
  const applyServerErrors = useCallback((error: ApiError | null, fieldMap: Partial<Record<string, keyof V>> = {}) => {
    if (!error) {
      setServerErrors({});
      return [];
    }
    const mapped: Partial<Record<keyof V, string>> = {};
    const unmatched: string[] = [];
    for (const fe of error.fieldErrors) {
      const key = fieldMap[fe.field] ?? (fe.field in initial ? (fe.field as keyof V) : undefined);
      if (key) mapped[key] = fe.message;
      else unmatched.push(fe.message);
    }
    setServerErrors(mapped);
    return unmatched;
  }, [initial]);

  const reset = useCallback((next: V) => {
    setValues(next);
    setBaseline(next);
    setTouched({});
    setSubmitted(false);
    setServerErrors({});
  }, []);

  return {
    values,
    setValue,
    touch,
    errorFor,
    clientErrors,
    isValid,
    isDirty,
    submitted,
    setSubmitted,
    applyServerErrors,
    markClean: () => setBaseline(values),
    reset,
  };
}
