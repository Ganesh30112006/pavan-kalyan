import './feedback.css';

interface TableSkeletonProps {
  columns: number;
  rows?: number;
  label?: string;
}

/** Placeholder rows shaped like the table that is loading. */
export function TableSkeleton({ columns, rows = 5, label = 'Loading' }: TableSkeletonProps) {
  return (
    <div role="status" aria-live="polite">
      <span className="visually-hidden">{label}…</span>
      <table className="table-skeleton data-table" aria-hidden="true">
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: columns }, (_, c) => (
                <td key={c}>
                  <span className="skeleton" style={{ width: `${c === 0 ? 70 : 40 + ((r + c) % 3) * 15}%` }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
