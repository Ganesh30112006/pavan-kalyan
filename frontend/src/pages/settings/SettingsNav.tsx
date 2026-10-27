import { SubNav } from '../../components/navigation/SubNav';

/**
 * Every section is always listed. Sections the backend does not support open a page
 * that says so, rather than disappearing without explanation.
 */
export function SettingsNav() {
  return (
    <SubNav
      label="Settings sections"
      items={[
        { to: '/settings/profile', label: 'Profile and preferences' },
        { to: '/settings/organization', label: 'Organization and team' },
        { to: '/settings/notifications', label: 'Notifications' },
        { to: '/settings/alerts', label: 'Alerts' },
        { to: '/settings/api-tokens', label: 'API tokens' },
        { to: '/settings/sessions', label: 'Session' },
        { to: '/settings/platform', label: 'Platform' },
      ]}
    />
  );
}
