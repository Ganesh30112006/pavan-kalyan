import { NavLink } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { NAV_ITEMS } from './navItems';
import './navigation.css';

interface SidebarNavProps {
  onNavigate?: () => void;
  /** Icon-only rail on desktop. Each link keeps its accessible name and a tooltip. */
  collapsed?: boolean;
}

export function SidebarNav({ onNavigate, collapsed = false }: SidebarNavProps) {
  const { can } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.permission || can(item.permission));
  return (
    <nav className={`sidebar-nav${collapsed ? ' sidebar-nav--collapsed' : ''}`} aria-label="Main">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className="sidebar-nav__link"
          onClick={onNavigate}
          aria-label={collapsed ? label : undefined}
          title={collapsed ? label : undefined}
        >
          <Icon size={18} aria-hidden="true" />
          <span className="sidebar-nav__label">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
