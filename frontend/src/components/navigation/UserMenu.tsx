import { ChevronDown, LogOut, Settings, UserRound } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { initials } from '../../utils/format';
import { Button } from '../forms/Button';
import './navigation.css';

export function UserMenu() {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const first = rootRef.current?.querySelector<HTMLElement>('[role="menuitem"]');
    first?.focus();
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const items = Array.from(rootRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
      const index = items.indexOf(document.activeElement as HTMLElement);
      const next = event.key === 'ArrowDown' ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
      items[next]?.focus();
      event.preventDefault();
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!session) return null;
  const close = () => setOpen(false);

  return (
    <div className="user-menu" ref={rootRef}>
      <Button
        ref={triggerRef}
        variant="ghost"
        className="user-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Account menu for ${session.user.display_name}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar" aria-hidden="true">
          {initials(session.user.display_name)}
        </span>
        <span className="user-menu__name">{session.user.display_name}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </Button>
      {open && (
        <ul className="user-menu__list" role="menu" id={menuId} aria-label="Account">
          <li role="none" className="user-menu__header">
            <div>{session.user.display_name}</div>
            <div className="text-muted">{session.user.email}</div>
          </li>
          <li role="none">
            <Link role="menuitem" className="user-menu__item" to="/settings/profile" onClick={close}>
              <UserRound size={16} aria-hidden="true" /> Profile and preferences
            </Link>
          </li>
          <li role="none">
            <Link role="menuitem" className="user-menu__item" to="/settings/sessions" onClick={close}>
              <Settings size={16} aria-hidden="true" /> Session and platform
            </Link>
          </li>
          <li role="none">
            <Link role="menuitem" className="user-menu__item" to="/logout" onClick={close}>
              <LogOut size={16} aria-hidden="true" /> Sign out
            </Link>
          </li>
        </ul>
      )}
    </div>
  );
}
