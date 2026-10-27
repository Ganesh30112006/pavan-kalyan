import './feedback.css';

interface InlineLoaderProps {
  label: string;
  /** Hide the label visually but keep it for screen readers. */
  hideLabel?: boolean;
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function InlineLoader({ label, hideLabel = false }: InlineLoaderProps) {
  return (
    <span className="inline-loader" role="status">
      <Spinner />
      <span className={hideLabel ? 'visually-hidden' : undefined}>{label}</span>
    </span>
  );
}
