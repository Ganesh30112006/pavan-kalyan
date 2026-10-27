import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useId } from 'react';
import { PAGE_SIZE_OPTIONS } from '../../hooks/usePaginationParams';
import { Button } from '../forms/Button';
import './table.css';

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  itemLabel: string;
  onPageChange(page: number): void;
  onPageSizeChange(size: number): void;
}

export function Pagination({ page, pageSize, total, itemLabel, onPageChange, onPageSizeChange }: PaginationProps) {
  const sizeId = useId();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return (
    <nav className="pagination" aria-label={`${itemLabel} pages`}>
      <span>
        {total === 0 ? `No ${itemLabel}` : `${first}–${last} of ${total.toLocaleString()} ${itemLabel}`}
      </span>
      <div className="pagination__controls">
        <label htmlFor={sizeId}>Rows per page</label>
        <select id={sizeId} className="select" value={pageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          iconOnly
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </Button>
        <span aria-live="polite">
          Page {Math.min(page, pageCount)} of {pageCount}
        </span>
        <Button
          size="sm"
          iconOnly
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight size={16} aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
