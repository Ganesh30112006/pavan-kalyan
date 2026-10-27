import type { HttpClient } from './client';
import type { AuditApi } from './contract';
import { auditEvent, paginated } from './decoders';
import { endpoints } from './endpoints';

export function createAuditApi(http: HttpClient): AuditApi {
  return {
    list: (params, signal) =>
      http.request(
        {
          path: endpoints.audit.list,
          query: {
            page: params.page,
            page_size: params.page_size,
            search: params.search,
            actor: params.actor,
            action: params.action,
            outcome: params.outcome,
            resource_type: params.resource_type,
            from: params.from,
            to: params.to,
          },
          signal,
        },
        paginated(auditEvent),
      ),
  };
}
