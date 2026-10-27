import { Boxes, CornerDownLeft, Search } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../../hooks/useApi';
import { useAuth } from '../../hooks/useAuth';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { useResource } from '../../hooks/useResource';
import { describeError } from '../../services/api/errors';
import type { ApplicationSummary } from '../../types/api';
import { providerLabel } from '../../utils/providers';
import { Dialog } from '../dialogs/Dialog';
import { Spinner } from '../feedback/InlineLoader';
import { NAV_ITEMS } from './navItems';
import './navigation.css';

interface Command {
  id: string;
  label: string;
  detail?: string;
  group: 'Pages' | 'Actions' | 'Applications';
  to: string;
}

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
}

function CommandPalette({ onClose }: { onClose(): void }) {
  const api = useApi();
  const { can } = useAuth();
  const navigate = useNavigate();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const search = useDebouncedValue(query.trim(), 250);

  const apps = useResource(
    `command-search:${search}`,
    (signal) => api.applications.list({ page: 1, page_size: 5, search }, signal),
    { enabled: search.length > 0 },
  );

  const staticCommands = useMemo((): Command[] => {
    const pages: Command[] = NAV_ITEMS.filter((i) => !i.permission || can(i.permission)).map((i) => ({
      id: `page:${i.to}`,
      label: i.label,
      group: 'Pages',
      to: i.to,
    }));
    const actions: Command[] = [];
    if (can('applications:create'))
      actions.push({
        id: 'action:new-app',
        label: 'Create application',
        group: 'Actions',
        to: '/applications/new',
      });
    if (can('providers:manage'))
      actions.push({
        id: 'action:new-provider',
        label: 'Connect a cloud provider',
        group: 'Actions',
        to: '/providers/new',
      });
    actions.push({
      id: 'action:failed',
      label: 'View failed deployments',
      group: 'Actions',
      to: '/deployments?status=failed',
    });
    return [...pages, ...actions];
  }, [can]);

  const q = query.trim().toLowerCase();
  const matches = staticCommands.filter((c) => !q || c.label.toLowerCase().includes(q));
  // Keep the previous results on screen while the next search loads, so the list does not empty and
  // refill (and options under the pointer do not disappear) on every keystroke.
  // (State adjusted during render, React's pattern for deriving from changing inputs.)
  const [shownApps, setShownApps] = useState<ApplicationSummary[]>([]);
  const latestApps = search && apps.status === 'success' && apps.data ? apps.data.items : null;
  if (latestApps && latestApps !== shownApps) setShownApps(latestApps);
  else if ((!search || apps.status === 'error') && shownApps.length > 0) setShownApps([]);
  const appCommands: Command[] = search
    ? shownApps.map((a) => ({
        id: `app:${a.id}`,
        label: a.name,
        detail: `${providerLabel(a.provider)} · ${a.region}`,
        group: 'Applications',
        to: `/applications/${encodeURIComponent(a.id)}`,
      }))
    : [];
  const results = [...matches, ...appCommands];
  const activeIndex = Math.min(active, Math.max(results.length - 1, 0));

  const go = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    navigate(command.to);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((activeIndex + 1) % Math.max(results.length, 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((activeIndex - 1 + results.length) % Math.max(results.length, 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      go(results[activeIndex]);
    }
  };

  const optionId = (index: number) => `${listId}-option-${index}`;
  let lastGroup: string | null = null;

  return (
    <Dialog open title="Search CloudBridge" onClose={onClose} initialFocusRef={inputRef}>
      <div className="command">
        <div className="command__input-row">
          <Search size={16} aria-hidden="true" />
          <input
            ref={inputRef}
            className="command__input"
            role="combobox"
            aria-label="Search pages, actions and applications"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={results.length ? optionId(activeIndex) : undefined}
            placeholder="Search pages, actions and applications…"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          {apps.status === 'loading' && search && <Spinner />}
        </div>
        <ul className="command__list" role="listbox" id={listId} aria-label="Results">
          {results.map((command, index) => {
            const heading = command.group !== lastGroup ? command.group : null;
            lastGroup = command.group;
            return (
              <li
                key={command.id}
                id={optionId(index)}
                role="option"
                aria-selected={index === activeIndex}
                className={`command__option${index === activeIndex ? ' command__option--active' : ''}`}
                onMouseMove={() => setActive(index)}
                onClick={() => go(command)}
              >
                {heading && (
                  <span className="command__group" aria-hidden="true">
                    {heading}
                  </span>
                )}
                <span className="command__label">
                  {command.group === 'Applications' && <Boxes size={14} aria-hidden="true" />}
                  {command.label}
                  {command.detail && <span className="text-muted text-small"> · {command.detail}</span>}
                </span>
                {index === activeIndex && <CornerDownLeft size={14} aria-hidden="true" className="command__enter" />}
              </li>
            );
          })}
        </ul>
        {results.length === 0 && apps.status !== 'loading' && (
          <p className="command__empty" role="status">
            No pages, actions or applications match “{query}”.
          </p>
        )}
        {search && apps.status === 'error' && apps.error && (
          <p className="command__empty" role="status">
            Applications could not be searched: {describeError(apps.error).title.toLowerCase()}.
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Header entry point for searching pages and applications (Ctrl+K / ⌘K). */
export function CommandSearch() {
  const [open, setOpen] = useState(false);
  const mac = isMac();

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <button
        type="button"
        className="command-trigger"
        onClick={() => setOpen(true)}
        // The visible label is hidden on narrow screens, so the name cannot come from it there.
        aria-label="Search"
        aria-haspopup="dialog"
        aria-keyshortcuts={mac ? 'Meta+K' : 'Control+K'}
      >
        <Search size={15} aria-hidden="true" />
        <span className="command-trigger__label">Search…</span>
        <kbd className="command-trigger__kbd" aria-hidden="true">
          {mac ? '⌘K' : 'Ctrl K'}
        </kbd>
      </button>
      {open && <CommandPalette onClose={() => setOpen(false)} />}
    </>
  );
}
