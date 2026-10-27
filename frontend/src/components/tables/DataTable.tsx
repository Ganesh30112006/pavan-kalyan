import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SortDirection } from '../../types/api';
import './table.css';
import { TableScroll } from './TableScroll';

export interface Column<T> {
  key: string;
  header: string;
  render(row: T): ReactNode;
  className?: string;
  /** Header is still read by screen readers, but hidden visually (e.g. an actions column). */
  hideHeader?: boolean;
  /** Server-side sort key; makes the header a sort button when the table has onSortChange. */
  sortKey?: string;
}

export interface TableSort {
  key: string;
  direction: SortDirection;
}

interface DataTableProps<T> {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey(row: T): string;
  /** Set while a background refresh is running over the rows shown. */
  busy?: boolean;
  sort?: TableSort | null;
  onSortChange?(sort: TableSort): void;
}

function SortHeader({ label, active, onClick }: { label: string; active: SortDirection | null; onClick(): void }) {
  const Icon = active === 'asc' ? ArrowUp : active === 'desc' ? ArrowDown : ArrowUpDown;
  return (
    <button type="button" className={`data-table__sort${active ? ' data-table__sort--active' : ''}`} onClick={onClick}>
      {label}
      <Icon size={13} aria-hidden="true" />
      <span className="visually-hidden">
        {active ? `, sorted ${active === 'asc' ? 'ascending' : 'descending'}. Activate to reverse.` : ', not sorted. Activate to sort.'}
      </span>
    </button>
  );
}

/**
 * Plain semantic table. Below 40rem each row stacks into label/value pairs,
 * so tables stay readable on phones without horizontal scrolling.
 */
export function DataTable<T>({ caption, columns, rows, rowKey, busy = false, sort = null, onSortChange }: DataTableProps<T>) {
  return (
    <TableScroll label={caption}>
      <table className="data-table data-table--stack" aria-busy={busy || undefined}>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((col) => {
              const sortable = Boolean(col.sortKey && onSortChange);
              const active = sortable && sort && sort.key === col.sortKey ? sort.direction : null;
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={col.className}
                  aria-sort={sortable ? (active === 'asc' ? 'ascending' : active === 'desc' ? 'descending' : 'none') : undefined}
                >
                  {col.hideHeader ? (
                    <span className="visually-hidden">{col.header}</span>
                  ) : sortable ? (
                    <SortHeader
                      label={col.header}
                      active={active}
                      onClick={() =>
                        onSortChange?.({ key: col.sortKey!, direction: active === 'asc' ? 'desc' : 'asc' })
                      }
                    />
                  ) : (
                    col.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((col) => (
                <td key={col.key} className={col.className} data-label={col.hideHeader ? '' : col.header}>
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
