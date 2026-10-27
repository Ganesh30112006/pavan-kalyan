import { useEffect, useRef, useState } from 'react';
import { useDebouncedValue } from './useDebouncedValue';

/**
 * A search box backed by a URL parameter. Typing is written to the URL once it settles; when the URL
 * changes elsewhere (a "clear" button, back/forward, a link), the box follows it instead of writing
 * its stale debounced text back over the new value.
 */
export function useSyncedSearch(
  urlValue: string,
  push: (value: string) => void,
  delayMs = 300,
): [string, (value: string) => void] {
  const [text, setText] = useState(urlValue);
  const debounced = useDebouncedValue(text.trim(), delayMs);
  const pushed = useRef<string | null>(null);
  const previousUrl = useRef(urlValue);

  useEffect(() => {
    if (urlValue !== previousUrl.current) {
      previousUrl.current = urlValue;
      if (urlValue !== pushed.current) {
        setText(urlValue);
        return;
      }
    }
    if (debounced === text.trim() && debounced !== urlValue) {
      pushed.current = debounced;
      push(debounced);
    }
  }, [urlValue, debounced, text, push]);

  return [text, setText];
}
