import { Navigate, Outlet, createBrowserRouter, type RouteObject } from 'react-router-dom';
import { AppShell } from '../components/layout/AppShell';
import { ApplicationConfigurationTab } from '../pages/application-details/ApplicationConfigurationTab';
import { ApplicationDeploymentsTab } from '../pages/application-details/ApplicationDeploymentsTab';
import { ApplicationLayout } from '../pages/application-details/ApplicationLayout';
import { ApplicationLogsTab } from '../pages/application-details/ApplicationLogsTab';
import { AlertsPage } from '../pages/settings/AlertsPage';
import { ApiTokensPage } from '../pages/settings/ApiTokensPage';
import { ApplicationAutomationTab } from '../pages/application-details/ApplicationAutomationTab';
import { ApplicationDomainsTab } from '../pages/application-details/ApplicationDomainsTab';
import { ApplicationMetricsTab } from '../pages/application-details/ApplicationMetricsTab';
import { ApplicationOverviewTab } from '../pages/application-details/ApplicationOverviewTab';
import { ApplicationsPage } from '../pages/applications/ApplicationsPage';
import { CreateApplicationPage } from '../pages/applications/CreateApplicationPage';
import { AuditLogPage } from '../pages/audit/AuditLogPage';
import { LoginPage } from '../pages/auth/LoginPage';
import { LogoutPage } from '../pages/auth/LogoutPage';
import { DeploymentDetailsPage } from '../pages/deployments/DeploymentDetailsPage';
import { DeploymentsPage } from '../pages/deployments/DeploymentsPage';
import { AccessDeniedPage } from '../pages/errors/AccessDeniedPage';
import { GeneralErrorPage, RouteErrorPage } from '../pages/errors/GeneralErrorPage';
import { MaintenancePage } from '../pages/errors/MaintenancePage';
import { NotFoundPage } from '../pages/errors/NotFoundPage';
import { ServiceUnavailablePage } from '../pages/errors/ServiceUnavailablePage';
import { OverviewPage } from '../pages/overview/OverviewPage';
import { ProviderConnectionPage } from '../pages/providers/ProviderConnectionPage';
import { ProviderSetupPage } from '../pages/providers/ProviderSetupPage';
import { ProvidersPage } from '../pages/providers/ProvidersPage';
import { NotificationsPage } from '../pages/settings/NotificationsPage';
import { OrganizationPage } from '../pages/settings/OrganizationPage';
import { PlatformPage } from '../pages/settings/PlatformPage';
import { ProfilePage } from '../pages/settings/ProfilePage';
import { SessionsPage } from '../pages/settings/SessionsPage';
import { RequireAuth } from './RequireAuth';

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <Outlet />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: 'login', element: <LoginPage /> },
      { path: 'logout', element: <LogoutPage /> },
      { path: 'unavailable', element: <ServiceUnavailablePage /> },
      { path: 'maintenance', element: <MaintenancePage /> },
      { path: 'error', element: <GeneralErrorPage /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { index: true, element: <OverviewPage /> },
              { path: 'applications', element: <ApplicationsPage /> },
              { path: 'applications/new', element: <CreateApplicationPage /> },
              {
                path: 'applications/:applicationId',
                element: <ApplicationLayout />,
                children: [
                  { index: true, element: <ApplicationOverviewTab /> },
                  { path: 'deployments', element: <ApplicationDeploymentsTab /> },
                  { path: 'configuration', element: <ApplicationConfigurationTab /> },
                  { path: 'logs', element: <ApplicationLogsTab /> },
                  { path: 'metrics', element: <ApplicationMetricsTab /> },
                  { path: 'domains', element: <ApplicationDomainsTab /> },
                  { path: 'automation', element: <ApplicationAutomationTab /> },
                ],
              },
              { path: 'deployments', element: <DeploymentsPage /> },
              { path: 'deployments/:deploymentId', element: <DeploymentDetailsPage /> },
              { path: 'providers', element: <ProvidersPage /> },
              { path: 'providers/new', element: <ProviderSetupPage /> },
              { path: 'providers/:connectionId', element: <ProviderConnectionPage /> },
              { path: 'activity', element: <AuditLogPage /> },
              { path: 'settings', element: <Navigate to="/settings/profile" replace /> },
              { path: 'settings/profile', element: <ProfilePage /> },
              { path: 'settings/organization', element: <OrganizationPage /> },
              { path: 'settings/notifications', element: <NotificationsPage /> },
              { path: 'settings/alerts', element: <AlertsPage /> },
              { path: 'settings/api-tokens', element: <ApiTokensPage /> },
              { path: 'settings/sessions', element: <SessionsPage /> },
              { path: 'settings/platform', element: <PlatformPage /> },
              { path: 'access-denied', element: <AccessDeniedPage /> },
              { path: '*', element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
