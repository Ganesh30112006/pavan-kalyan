import { Activity, Boxes, Cloud, LayoutDashboard, Rocket, Settings, type LucideIcon } from 'lucide-react';
import type { Permission } from '../../types/api';

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  permission?: Permission;
}

/** Main navigation, shared by the sidebar and command search. */
export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/applications', label: 'Applications', icon: Boxes },
  { to: '/deployments', label: 'Deployments', icon: Rocket },
  { to: '/providers', label: 'Cloud providers', icon: Cloud },
  { to: '/activity', label: 'Activity log', icon: Activity, permission: 'audit:read' },
  // Matches every /settings/* page, so the item stays highlighted in all settings sections.
  { to: '/settings', label: 'Settings', icon: Settings },
];
