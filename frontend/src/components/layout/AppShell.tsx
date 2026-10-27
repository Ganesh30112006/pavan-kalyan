import { Building2, Menu, Network, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { NetworkStatus } from '../feedback/NetworkStatus';
import { Button } from '../forms/Button';
import { CommandSearch } from '../navigation/CommandSearch';
import { NotificationsMenu } from '../navigation/NotificationsMenu';
import { SidebarNav } from '../navigation/SidebarNav';
import { UserMenu } from '../navigation/UserMenu';
import { GeneralErrorContent } from '../../pages/errors/GeneralErrorPage';
import { DemoBanner } from './DemoBanner';
import { ErrorBoundary } from './ErrorBoundary';
import './layout.css';

export function AppShell() {
  const { session } = useAuth();
  const location = useLocation();
  // The menu belongs to the page it was opened on, so it closes itself on navigation.
  const [menuOpenOn, setMenuOpenOn] = useState<string | null>(null);
  const menuOpen = menuOpenOn === location.pathname;
  const setMenuOpen = (open: boolean) => setMenuOpenOn(open ? location.pathname : null);
  // Desktop only: an icon rail. Not persisted, because browser storage is not used.
  const [collapsed, setCollapsed] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);

  // Move focus to the new page after navigation, so keyboard and screen-reader users start at its content.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    sidebarRef.current?.querySelector<HTMLElement>('a[href]')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpenOn(null);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [menuOpen]);

  return (
    <div className={`app-shell${collapsed ? ' app-shell--collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <div className="app-shell__banners">
        <DemoBanner />
        <NetworkStatus />
      </div>
      <header className="app-header">
        <Button
          ref={toggleRef}
          className="app-header__menu-toggle"
          variant="ghost"
          iconOnly
          aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={menuOpen}
          aria-controls="app-sidebar"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
        </Button>
        <Link to="/" className="app-header__brand" aria-label="CloudBridge overview">
          <span className="app-header__logo" aria-hidden="true">
            <Network size={16} />
          </span>
          <span className="app-header__name">CloudBridge</span>
        </Link>
        {session && (
          // The session carries a single organization; switching workspaces is not offered by the API.
          <span className="app-header__org" title="Current organization">
            <Building2 size={14} aria-hidden="true" />
            <span className="visually-hidden">Organization: </span>
            {session.organization.name}
          </span>
        )}
        <span className="app-header__spacer" />
        <CommandSearch />
        <NotificationsMenu />
        <UserMenu />
      </header>
      <div
        className={`sidebar-backdrop${menuOpen ? ' sidebar-backdrop--open' : ''}`}
        onClick={() => setMenuOpen(false)}
        aria-hidden="true"
      />
      <aside id="app-sidebar" ref={sidebarRef} className={`app-sidebar${menuOpen ? ' app-sidebar--open' : ''}`} aria-label="Sidebar">
        <div className="app-sidebar__mobile-header">
          <span className="app-header__brand">CloudBridge</span>
          <Button
            variant="ghost"
            iconOnly
            aria-label="Close navigation"
            onClick={() => {
              setMenuOpenOn(null);
              toggleRef.current?.focus();
            }}
          >
            <X size={18} aria-hidden="true" />
          </Button>
        </div>
        <SidebarNav onNavigate={() => setMenuOpen(false)} collapsed={collapsed} />
        <Button
          className="app-sidebar__collapse"
          variant="ghost"
          size="sm"
          iconOnly={collapsed}
          aria-label={collapsed ? 'Expand sidebar' : undefined}
          title={collapsed ? 'Expand sidebar' : undefined}
          aria-expanded={!collapsed}
          aria-controls="app-sidebar"
          onClick={() => setCollapsed((c) => !c)}
          icon={collapsed ? undefined : <PanelLeftClose size={16} aria-hidden="true" />}
        >
          {collapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : 'Collapse sidebar'}
        </Button>
      </aside>
      <main id="main-content" className="app-main" ref={mainRef} tabIndex={-1}>
        <ErrorBoundary resetKey={location.pathname} fallback={(reset) => <GeneralErrorContent onRetry={reset} />}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
