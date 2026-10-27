import { NavLink } from 'react-router-dom';
import './navigation.css';

interface SubNavProps {
  label: string;
  items: Array<{ to: string; label: string; end?: boolean }>;
}

/** Secondary navigation between views of the same resource. */
export function SubNav({ label, items }: SubNavProps) {
  return (
    <nav className="subnav" aria-label={label}>
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="subnav__link">
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
