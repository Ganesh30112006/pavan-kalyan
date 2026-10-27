import { array } from './decode';
import type { HttpClient } from './client';
import type { SettingsApi } from './contract';
import { notificationPreferences, organization, organizationMember, user } from './decoders';
import { endpoints } from './endpoints';

export function createSettingsApi(http: HttpClient): SettingsApi {
  return {
    updateProfile: (input) => http.request({ method: 'PATCH', path: endpoints.settings.me, body: input }, user),
    getOrganization: (signal) => http.request({ path: endpoints.settings.organization, signal }, organization),
    updateOrganization: (input) =>
      http.request({ method: 'PATCH', path: endpoints.settings.organization, body: input }, organization),
    listMembers: (signal) => http.request({ path: endpoints.settings.members, signal }, array(organizationMember)),
    getNotificationPreferences: (signal) =>
      http.request({ path: endpoints.settings.notificationPreferences, signal }, notificationPreferences),
    updateNotificationPreferences: (input) =>
      http.request(
        { method: 'PUT', path: endpoints.settings.notificationPreferences, body: input },
        notificationPreferences,
      ),
  };
}
