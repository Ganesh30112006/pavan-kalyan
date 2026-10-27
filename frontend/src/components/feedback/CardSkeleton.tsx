import './feedback.css';

interface CardSkeletonProps {
  lines?: number;
  label?: string;
}

export function CardSkeleton({ lines = 3, label = 'Loading' }: CardSkeletonProps) {
  return (
    <div className="card-skeleton" role="status" aria-live="polite">
      <span className="visually-hidden">{label}…</span>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton" aria-hidden="true" style={{ width: `${90 - i * 18}%` }} />
      ))}
    </div>
  );
}
