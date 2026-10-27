import { useAuth } from '../../hooks/useAuth';
import { formatDateTime, formatRelative } from '../../utils/format';

/** "5 minutes ago", with the full timestamp (in the user's preferred zone) available on hover and to screen readers. */
export function RelativeTime({ value }: { value: string | null | undefined }) {
  const { session } = useAuth();
  if (!value) return <span>—</span>;
  const absolute = formatDateTime(value, session?.user.preferences);
  return (
    <time dateTime={value} title={absolute}>
      {formatRelative(value)}
    </time>
  );
}

export function DateTime({ value }: { value: string | null | undefined }) {
  const { session } = useAuth();
  if (!value) return <span>—</span>;
  return <time dateTime={value}>{formatDateTime(value, session?.user.preferences)}</time>;
}
