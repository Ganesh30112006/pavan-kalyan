# CloudBridge API contract (frontend expectations)

The FastAPI backend in `backend/` implements this contract and its tests check
every response shape against it. The frontend checks every response at runtime
with `frontend/src/services/api/decoders.ts`; anything that does not match is
shown to the user as an "unexpected response" error and never used.

All paths are relative to `VITE_API_BASE_URL` and prefixed with `/api/v1`.
JSON field names are `snake_case`. Timestamps are ISO 8601 strings in UTC.

## Conventions

### Authentication and CSRF

- Cookie sessions: the backend sets an `HttpOnly; Secure; SameSite=Lax` (or
  `Strict`) session cookie on login. The frontend never sees or stores a token.
- All requests are sent with `credentials: 'include'`. For a cross-origin API,
  CORS must allow exactly the frontend origin with `Access-Control-Allow-Credentials: true`.
- CSRF (double submit): the backend sets a readable, non-HttpOnly `cb_csrf`
  cookie on any API response when the request had none (so the frontend's
  initial `GET /auth/session` provides it before sign-in), and a fresh one on login. The frontend copies it into the `X-CSRF-Token` header on every
  `POST`, `PUT`, `PATCH` and `DELETE`. The backend must reject unsafe requests
  whose header does not match the cookie.
- A `401` from any endpoint except `/auth/*` ends the session in the UI and
  returns the user to the sign-in page.
- API tokens (for the CLI and CI): `Authorization: Bearer cbt_…`. Requests
  with a bearer token are exempt from CSRF (no cookies are involved), act with
  the token owner's role, and cannot create more tokens. Only a SHA-256 hash
  of each token is stored.
- GitHub webhooks (`/hooks/github/{id}`) are authenticated by their
  `X-Hub-Signature-256` HMAC instead of a session and are exempt from CSRF.

### Request headers sent by the frontend

| Header            | When                                   | Purpose                                     |
| ----------------- | -------------------------------------- | ------------------------------------------- |
| `X-Request-ID`    | every request                          | Correlation ID (the backend may replace it) |
| `X-CSRF-Token`    | unsafe methods, when `cb_csrf` is set  | CSRF protection                             |
| `Idempotency-Key` | creating applications, deployments, rollbacks and provider connections | De-duplicates retried submissions |

The same `Idempotency-Key` is reused when the user re-submits an identical
payload (for example after a timeout). The backend should return the original
result for a repeated key, and `409` or `422` if the key is reused with a
different payload.

### Response headers

- `X-Request-ID`: echoed or generated; shown to users next to errors.
- `Retry-After`: on `429` and `503`, in seconds or as an HTTP date.
- `Allow`: on `405`, every method the path supports.
- Every API response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.

### Errors

Preferred envelope:

```json
{
  "error": {
    "code": "duplicate_name",
    "message": "An application with this name already exists.",
    "request_id": "req_01H…",
    "fields": [{ "field": "name", "message": "This name is already in use." }]
  }
}
```

FastAPI's defaults are also understood: `{"detail": "text"}` and
`{"detail": [{"loc": ["body", "source", "image"], "msg": "…"}]}` (the `loc`
becomes the dotted field `source.image`).

The frontend shows `message` only for `400`, `409` and `422`, and only after
removing anything that looks like a stack trace, SQL, a file path, an
internal host or a credential. It never shows server text for `5xx`. The
backend must still not put internal details in messages.

| Status          | Meaning in the UI                                                |
| --------------- | ---------------------------------------------------------------- |
| 400             | Invalid request (message shown)                                  |
| 401             | Not signed in / session expired                                  |
| 403             | Access denied (pages show an access-denied state, not empty data)|
| 404             | Resource not found; for optional settings endpoints, "not supported" |
| 409             | Conflict: duplicate, version mismatch, operation already running |
| 422             | Field validation errors (`fields`)                               |
| 429             | Rate limited (`Retry-After` shown)                               |
| 500             | Server error                                                     |
| 502 / 503 / 504 | Upstream unavailable or timed out                                |
| 503 + `code: "maintenance"` | Maintenance screen for the whole app                 |

Reads (`GET`) are retried at most twice on network errors, `429` (short
`Retry-After` only), `502`, `503` and `504`. Writes are never retried
automatically.

### Pagination

List endpoints take `page` (1-based) and `page_size` (10, 25 or 50) and return:

```json
{ "items": [], "total": 0, "page": 1, "page_size": 25 }
```

Lists are sorted newest first unless stated otherwise.

### Long-running operations

Deployments, rollbacks, provider validation and (optionally) deletion are
asynchronous. The backend answers `202 Accepted` with the current resource
and the frontend polls it:

- Deployments: every 3 s while in any active state (`queued` through `verifying`, or `rolling_back`),
  stopping after 30 minutes (the user can resume).
- Provider validation: every 2 s while `validating`, for at most 5 minutes.
- Lists containing running work: every 4–5 s, for at most 10–15 minutes.

Polling pauses while the browser tab is hidden and backs off after errors.
Server-sent events can replace polling later without changing the UI.

## Endpoints

### Session

| Method | Path              | Body                  | Response          |
| ------ | ----------------- | --------------------- | ----------------- |
| GET    | `/auth/session`   | —                     | `Session` or 401  |
| POST   | `/auth/login`     | `{ email, password }` | `Session`, sets cookies; 401 for bad credentials (same response whether or not the email exists); 429 when rate limited |
| POST   | `/auth/logout`    | —                     | 204, clears cookies |

```ts
Session = {
  user: { id, email, display_name, role: 'owner'|'admin'|'developer'|'viewer',
          preferences: { timezone: string|null, time_format: '12h'|'24h' } },
  organization: { id, name },
  permissions: Permission[],   // see below; unknown values are ignored
  features: { organization_settings: boolean, notification_preferences: boolean }
}
Permission = 'applications:create' | 'applications:update' | 'applications:delete'
  | 'deployments:create' | 'deployments:cancel' | 'deployments:rollback'
  | 'deployments:approve'          // owner and admin
  | 'providers:manage' | 'audit:read' | 'organization:manage'
```

Permissions only decide what the UI shows. Every endpoint must authorise the
request itself, including tenant isolation (a user can only reach resources of
their organization).

### Applications

| Method | Path                       | Notes |
| ------ | -------------------------- | ----- |
| GET    | `/apps?page&page_size&search&status&health&provider&sort` | `Paginated<ApplicationSummary>`. `health` is comma-separated (`unhealthy,degraded`); `sort` is `name`, `updated_at` or `health`, prefixed with `-` for descending |
| POST   | `/apps`                    | `CreateApplicationRequest` → 201 `Application`; `Idempotency-Key`; 422 with `fields` for settings the provider cannot run |
| GET    | `/apps/{id}`               | `Application` |
| DELETE | `/apps/{id}`               | 204 deleted, or 202 when removal (including cloud resource cleanup) continues in the background |
| GET    | `/apps/{id}/spec`          | `ApplicationConfiguration` (requested spec + effective provider configuration) |
| PUT    | `/apps/{id}/spec`          | `UpdateConfigurationRequest` (includes `version`) → `ApplicationConfiguration`; 409 on version mismatch |
| POST   | `/apps/{id}/validate`      | `SpecValidationResult`: checks the saved spec against the provider without deploying |
| GET    | `/apps/{id}/logs`          | `LogPage` of runtime logs. **Optional**: 404 or 501 is shown as "not available" |

```ts
ApplicationType = 'web_service'|'api'|'worker'
RuntimeType = 'node'|'python'|'java'|'go'|'dotnet'|'ruby'|'php'|'other'
HealthStatus = 'healthy'|'degraded'|'unhealthy'|'unknown'
ProviderKind = 'aws_app_runner'|'aws_ecs_express'|'gcp_cloud_run'|'azure_container_apps'|'kubernetes'
ApplicationSummary = { id, name, description|null, application_type, runtime,
  provider: ProviderKind, connection_id, region,
  status: 'draft'|'deploying'|'running'|'failed'|'stopped'|'deleting',  // 'stopped' = paused
  health: HealthStatus, current_version|null,
  latest_deployment: { id, status, created_at } | null, updated_at,
  environment: 'production'|'staging'|'preview',
  preview_of_id|null, preview_number|null,   // set on pull request previews
  paused: boolean }
Application = ApplicationSummary & { source: ApplicationSource, image_digest|null,   // sha256:… serving traffic
  service_url|null, created_at, created_by|null }
ApplicationSource = { type: 'image'|'repository', image|null, repository_url|null,
  ref_type: 'branch'|'commit'|null, git_ref|null, dockerfile_path|null, build_context|null }
RequestedConfiguration = { port, cpu, memory, min_instances, max_instances,
  concurrency: number|null,          // null = provider default
  exposure: 'public'|'private', health_check_path|null,
  environment: { name, value }[],
  secret_refs: { name, reference }[] } // Secrets Manager/SSM ARN, projects/…/secrets/…, a Key Vault
                                       // secret URL, or k8s:SECRET/KEY for Kubernetes
CreateApplicationRequest = RequestedConfiguration & { name, description|null, application_type, runtime,
  connection_id, region, source }
ApplicationConfiguration = RequestedConfiguration & { application_id, version, updated_at,
  effective: { port, cpu, memory, min_instances, max_instances, concurrency|null, exposure,
               health_check_path|null, observed_at } | null,   // null until first successful deployment
  adjustments: { field, requested, effective, reason }[] }
UpdateConfigurationRequest = RequestedConfiguration & { version }
SpecValidationResult = { valid, issues: { field|null, severity: 'error'|'warning', message }[], adjustments }
LogPage = { entries: { timestamp, stream: 'stdout'|'stderr'|'system', message }[], truncated }
```

CloudBridge stores secret **references** only. Secret values live in the
provider's secret manager and are never sent to, stored by, or returned by
CloudBridge. Log lines are redacted again in the UI before display.
A plain environment variable that carries a credential (`AWS_SECRET_ACCESS_KEY` and similar
names, or values such as private keys, AWS key IDs and GitHub, Slack or Stripe tokens) is
refused with `422` on `environment.N.value`, wherever the configuration comes from (the UI,
the API or a `cloudbridge.yaml`), and the value is never echoed back.

### Provider setup

`GET /providers/{provider}/setup` → `ConnectionSetup` (below) without an external ID: what a
customer's account must trust before any connection exists. Azure and AKS use it to run the setup
script before connecting, since the app registration's client ID is only known afterwards. Not
secret (connections show the same values); any signed-in user may read it. `404` for an unknown
provider.

### Provider capabilities

`GET /providers/{provider}/capabilities` → `ProviderCapabilities`. The UI
computes capability warnings (unsupported, adjusted, ignored settings) only
from this data; if it cannot be loaded the wizard says so and the backend
validates on submit.

```ts
ProviderCapabilities = { provider, regions: string[], cpu_options: string[], memory_options: string[],
  min_instances_floor,             // 0 = can scale to zero
  max_instances_limit, supports_concurrency, max_concurrency|null,
  supports_private_exposure, supported_application_types: ApplicationType[],
  supports_secret_references, notes: string[],
  features: { repository_builds, metrics, custom_domains, pause } }  // booleans
```

The UI hides the Metrics and Domains tabs, the pause control and repository
sources for providers whose `features` flag is false.

### Deployments

| Method | Path                                         | Notes |
| ------ | -------------------------------------------- | ----- |
| GET    | `/deployments?page&page_size&application_id&status&provider&search&sort` | `Paginated<DeploymentSummary>`. `status` is comma-separated; `sort` is `created_at`, `application_name` or `status` (`-` for descending) |
| GET    | `/apps/{id}/deployments?page&page_size`      | `Paginated<DeploymentSummary>` for one application |
| POST   | `/apps/{id}/deployments`                     | 202 `Deployment` (queued); `Idempotency-Key`; 409 if one is running |
| GET    | `/deployments/{id}`                          | `Deployment` |
| GET    | `/deployments/{id}/events`                   | `DeploymentEvent[]` |
| GET    | `/deployments/{id}/logs`                     | `LogPage` of build/rollout logs. **Optional**: 404 or 501 = not available |
| POST   | `/deployments/{id}/rollback`                 | 202 new `Deployment` with `kind: 'rollback'`; `Idempotency-Key`; 409 if not permitted |
| POST   | `/deployments/{id}/cancel`                   | **Optional.** Offered only when `allowed_actions.cancel` is true; also cancels a deployment awaiting approval |
| POST   | `/deployments/{id}/approve`                  | 202 `Deployment`; requires `deployments:approve`; 409 unless `awaiting_approval` |
| POST   | `/deployments/{id}/reject`                   | `Deployment` (ends as `cancelled`, failure code `rejected`); requires `deployments:approve` |

```ts
DeploymentStatus = 'awaiting_approval'|'queued'|'validating'|'building'|'publishing'|'deploying'|'verifying'
  |'succeeded'|'failed'|'rolling_back'|'rolled_back'|'failed_rollback'|'cancelled'
  // Upper-case values (QUEUED, FAILED_ROLLBACK, …) are accepted too.
DeploymentSummary = { id, application_id, application_name, provider, region|null, status,
  kind: 'deploy'|'rollback', version|null, image_digest|null, source_ref|null,
  initiated_by|null, created_at, finished_at|null,
  trigger: 'manual'|'push'|'pull_request'|'promotion'|'api' }
Deployment = DeploymentSummary & { rollback_of|null, started_at|null,
  stages: { name: 'queued'|'validation'|'build'|'publishing'|'provisioning'|'verification',
            status: 'pending'|'active'|'succeeded'|'failed'|'cancelled'|'skipped',
            started_at|null, finished_at|null }[],
  failure: { code, message } | null, service_url|null,
  progress_percent: number|null,   // only real provider-reported progress, never an estimate
  approved_by|null, approved_at|null,
  allowed_actions: { rollback: boolean, cancel: boolean, approve: boolean } }  // missing = false; the UI never assumes
DeploymentEvent = { id, timestamp, level: 'info'|'warning'|'error', stage|null, message }
```

A deployment whose verification fails and is automatically restored ends as
`rolled_back` (kind `deploy`); a rollback deployment ends as `rolled_back` on
success or `failed_rollback` on failure.

When an application has `requires_approval`, every new deployment except a
rollback starts as `awaiting_approval`; the worker does not touch it until an
owner or admin approves it, and an `approval_requested` alert is sent.

### Cloud connections

| Method | Path                               | Notes |
| ------ | ---------------------------------- | ----- |
| GET    | `/cloud-connections`               | `ProviderConnection[]` |
| POST   | `/cloud-connections`               | `CreateConnectionRequest` → 201 (`pending_validation`, with `setup`); `Idempotency-Key` |
| GET    | `/cloud-connections/{id}`          | `ProviderConnection` |
| POST   | `/cloud-connections/{id}/test`     | 202 (`validating`) |
| DELETE | `/cloud-connections/{id}`          | 204 or 202; 409 `connection_in_use` while applications use it |

```ts
CreateConnectionRequest = { name, provider, default_region,
  aws?: { account_id, role_arn },
  gcp?: { project_id, workload_identity_provider, service_account_email },
  azure?: { tenant_id, subscription_id, client_id, resource_group, environment_name, registry_name?|null },
  ecs?: { account_id, role_arn, cluster?|null },        // null = the account's default cluster
  kubernetes?: { api_server, ca_certificate, namespace,  // ca_certificate = PEM
                 auth: 'aks'|'oidc', tenant_id?|null, client_id?|null, audience?|null } }
ProviderConnection = { id, name, provider, default_region, created_at, last_validated_at|null,
  status: 'pending_validation'|'validating'|'connected'|'invalid_credentials'
        |'insufficient_permissions'|'provider_unavailable'|'timed_out'|'disconnected'|'error',
  config: { account_id|null, role_arn|null, project_id|null, workload_identity_provider|null, service_account_email|null,
            tenant_id|null, subscription_id|null, client_id|null, resource_group|null, environment_name|null,
            registry_name|null, ecs_cluster|null, api_server|null, namespace|null,
            kubernetes_auth: 'aks'|'oidc'|null, token_audience|null },
  setup: ConnectionSetup | null,
  validation: { stage: 'connecting'|'validating_permissions'|'checking_readiness'|null,
                started_at|null, finished_at|null, error_code|null,
                checks: { name, status: 'pending'|'passed'|'failed'|'skipped', detail|null }[] } | null }
```

```ts
ConnectionSetup = { external_id|null, trusted_principal|null, federation_issuer|null, federation_audience|null,
  // AWS only: the names CloudBridge uses in the account, which the setup script creates
  aws: { ecr_repository_prefix, apprunner_access_role, apprunner_instance_role, codebuild_role,
         ecs_execution_role, ecs_infrastructure_role } | null }
```

No long-lived cloud credentials are accepted: AWS uses `sts:AssumeRole` with a
per-connection external ID generated by the backend; Google Cloud uses
workload identity federation; Azure uses a federated identity credential on the
customer's app registration (`setup.federation_issuer`, `setup.trusted_principal`
as the subject, and `setup.federation_audience`), so no client secret exists.
Azure apps are created in the connection's resource group and Container Apps
environment, which must be in the connection's region. ECS Express Mode uses
the same role setup as App Runner. Kubernetes connections never take a
kubeconfig or service-account token: `auth: 'aks'` exchanges the platform's
federated assertion for an Entra token for the AKS server (the customer grants
"Azure Kubernetes Service RBAC Writer" on the namespace), and `auth: 'oidc'`
sends the assertion itself to a cluster configured to trust the issuer with
`audience`. CloudBridge only works inside the connection's namespace.
`validation.stage` should be reported while
validating; if it is `null` the UI shows an indeterminate indicator instead
of inventing steps.

### Activity log

`GET /audit-events?page&page_size&search&actor&action&outcome&resource_type&from&to` →
`Paginated<AuditEvent>` (requires `audit:read`).

```ts
AuditEvent = { id, occurred_at, actor: { id, display_name, email|null } | null,
  action, resource_type, resource_id|null, resource_name|null,
  outcome: 'success'|'failure'|'denied', request_id|null }
```

### Settings

| Method | Path                              | Notes |
| ------ | --------------------------------- | ----- |
| PATCH  | `/me`                             | `{ display_name, preferences }` → `User` |
| GET    | `/me/notification-preferences`    | `{ email_enabled, events: Record<event, boolean> }`; 404 = not supported |
| PUT    | `/me/notification-preferences`    | same shape |
| GET    | `/organization`                   | `{ id, name, created_at }`; 404 = not supported |
| PATCH  | `/organization`                   | `{ name }` (requires `organization:manage`) |
| GET    | `/organization/members`           | `{ id, display_name, email, role, joined_at }[]` |

Notification events: `deployment_succeeded`, `deployment_failed`,
`rollback_completed`, `provider_connection_failed`, `application_deleted`,
`application_unhealthy`, `approval_requested`.

### Automation, environments and GitHub webhooks

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/apps/{id}/automation` | `Automation` |
| PUT    | `/apps/{id}/automation` | `AutomationInput` → `Automation` (requires `applications:update`) |
| POST   | `/apps/{id}/webhook-secret` | `{ url, secret }`: creates or rotates the GitHub webhook secret; shown once |
| POST   | `/hooks/github/{id}` | Called by GitHub. 401 `invalid_signature` for a bad or missing signature (also for unknown applications); `{ outcome }` with 202 when a deployment was queued, otherwise 200 |
| PUT    | `/apps/{id}/config-file` | `{ content, version }` → `ApplicationConfiguration`: applies a `cloudbridge.yaml`; 409 on version mismatch, 422 with `fields` |
| GET    | `/apps/{id}/previews` | `ApplicationSummary[]` of the application's pull request previews |
| POST   | `/apps/{id}/promote` | 202 `Deployment` on the `promote_to_id` application, trigger `promotion`, using the image digest that is live here; `Idempotency-Key` |

```ts
Automation = { environment: 'production'|'staging'|'preview', promote_to_id|null,
  auto_deploy, auto_deploy_branch|null,   // null = the source branch
  previews_enabled, config_from_repo, requires_approval,
  webhook: { url, secret_configured } }
AutomationInput = Omit<Automation, 'webhook'>
```

- Webhooks are verified with HMAC-SHA256 over the raw body, de-duplicated by
  `X-GitHub-Delivery`, and ignored for other repositories and fork pull requests.
- A push to the auto-deploy branch deploys exactly the pushed commit; while a
  deployment runs it is deferred and deployed afterwards (latest push wins).
- `opened`/`reopened`/`synchronize` pull requests create or update the preview
  application `<name>-pr-<number>` (same connection and settings);
  `closed` deletes it, including its cloud resources.
- Promotion requires both applications to use the same provider.
- With `config_from_repo`, the worker reads `cloudbridge.yaml` at the deployed
  commit during validation and merges it into the saved settings (it may set
  `port`, `cpu`, `memory`, `instances: {min, max}`, `concurrency`, `exposure`,
  `health_check_path`, `environment` and `secrets` references, never secret values).

### Sleep schedules and pausing

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/apps/{id}/schedule` | `ScheduleState` |
| PUT    | `/apps/{id}/schedule` | `SleepSchedule` → `ScheduleState` |
| DELETE | `/apps/{id}/schedule` | `ScheduleState` (an app asleep stays paused until resumed) |
| POST   | `/apps/{id}/pause` | `ScheduleState`; 409 `not_deployed`, or 409 `not_supported` when the provider cannot pause |
| POST   | `/apps/{id}/resume` | `ScheduleState` |

```ts
SleepSchedule = { timezone,            // IANA name
  days: number[],                      // 0 = Monday
  sleep_at: 'HH:MM', wake_at: 'HH:MM' } // overnight windows allowed
ScheduleState = { schedule: SleepSchedule|null, paused, paused_by_schedule, asleep_now }
```

A paused application has status `stopped`. App Runner pauses the service, Azure
stops the container app, ECS and Kubernetes scale to zero tasks or replicas.

### Metrics and cost estimates

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/apps/{id}/metrics?range=1h\|6h\|24h\|7d` | `Metrics`; `unavailable_reason` instead of an error when the provider has no data |
| GET    | `/apps/{id}/cost-estimate` | `CostEstimate` for the saved settings |
| POST   | `/cost-estimate` | `{ provider, cpu, memory, min_instances, max_instances }` → `CostEstimate` (used by the create wizard) |

```ts
Metrics = { range, step_seconds, unavailable_reason|null,
  series: { name: 'requests'|'errors_4xx'|'errors_5xx'|'latency_ms'|'instances'|'cpu_percent'|'memory_percent',
            unit, points: { timestamp, value }[] }[] }
CostEstimate = { provider, currency: 'USD', monthly_low|null, monthly_high|null,
  lines: { item, low, high }[], assumptions: string[] }
```

Estimates use public list prices and are labelled as estimates; Kubernetes
returns `null` totals (the cluster is already paid for).

### Custom domains

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/apps/{id}/domains` | `CustomDomain[]` |
| POST   | `/apps/{id}/domains` | `{ domain }` → 201 `CustomDomain` with the DNS records to create |
| POST   | `/apps/{id}/domains/{domain_id}/check` | `CustomDomain`: re-checks DNS and certificate state |
| DELETE | `/apps/{id}/domains/{domain_id}` | 202; the domain is detached from the provider in the background |

```ts
CustomDomain = { id, domain, status: 'pending_dns'|'verifying'|'active'|'failed'|'removing',
  records: { type, name, value, purpose }[], message|null, created_at, checked_at|null }
```

### Alert channels

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/alert-channels` | `AlertChannel[]` |
| POST   | `/alert-channels` | `AlertChannelInput` → 201 `AlertChannel & { signing_secret|null }` (generic webhooks; shown once) |
| PUT    | `/alert-channels/{id}` | `AlertChannelInput` (omit `url` to keep it) → `AlertChannel` |
| DELETE | `/alert-channels/{id}` | 204 |
| POST   | `/alert-channels/{id}/test` | `{ delivered, status }` |

```ts
AlertChannel = { id, name, kind: 'slack'|'teams'|'webhook',
  host,                 // only the host; the full address is never returned
  events: NotificationEvent[], enabled, signed, created_at,
  last_delivery_at|null, last_delivery_status|null }
AlertChannelInput = { name, kind, url?, events, enabled }
```

Channel addresses must be `https://` on public IP addresses (Slack and Teams
addresses must be on their own domains). Generic webhooks are signed with
`X-CloudBridge-Signature: sha256=<HMAC of the body>`. Creating, changing,
deleting and testing channels require `providers:manage` (owner and admin).

### API tokens

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET    | `/me/api-tokens` | `ApiToken[]` |
| POST   | `/me/api-tokens` | `{ name, expires_in_days|null }` → 201 `ApiToken & { token }` (shown once); browser session only |
| DELETE | `/me/api-tokens/{id}` | 204; revoked immediately |

```ts
ApiToken = { id, name, prefix, created_at, last_used_at|null, expires_at|null }
```

## Not yet in the contract

These are intentionally not offered in the UI until the backend supports them:
member invitations and role changes, listing or revoking other sessions,
switching organizations, a notifications feed (the header shows live failures
from the deployments and connections endpoints instead), and log streaming
(logs are fetched on demand).
