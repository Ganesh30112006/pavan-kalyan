import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Horizontal scroll container for a table. While the table is wider than the space it has, the container
 * becomes a labelled, focusable region so keyboard users can scroll it too (WCAG 2.1.1); otherwise it adds
 * no extra tab stop.
 */
export function TableScroll({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollable, setScrollable] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    // ResizeObserver reports once when observation starts, then on every size change.
    const observer = new ResizeObserver(() => setScrollable(element.scrollWidth > element.clientWidth + 1));
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className="table-scroll"
      {...(scrollable ? { tabIndex: 0, role: 'region', 'aria-label': label } : {})}
    >
      {children}
    </div>
  );
}
