import type { ReactNode } from 'react';
import { useAuth } from '../hooks/useAuth';
import { AccessDeniedPage } from '../pages/errors/AccessDeniedPage';
import type { Permission } from '../types/api';

interface RequirePermissionProps {
  permission: Permission;
  resource: string;
  children: ReactNode;
}

/** Shows "access denied" instead of a page the user's role cannot use. Display logic only. */
export function RequirePermission({ permission, resource, children }: RequirePermissionProps) {
  const { can } = useAuth();
  return can(permission) ? <>{children}</> : <AccessDeniedPage resource={resource} />;
}
