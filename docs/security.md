# Security notes

The browser is an untrusted client. The frontend reduces mistakes and
exposure, but **it does not make the platform secure on its own**. This page
lists what the frontend does and what the backend, the deployment worker and
cloud IAM must enforce.

## What the frontend does

- **No tokens in JavaScript.** Authentication relies on an HttpOnly session
  cookie. Nothing is written to `localStorage` or `sessionStorage` (enforced by
  an ESLint rule). Passwords and secret values exist only in component state
  while a form is open.
- **CSRF header.** Unsafe requests carry `X-CSRF-Token` copied from the
  `cb_csrf` cookie (double submit).
- **No secret material in the UI.** Provider setup asks only for identifiers
  (role ARN, project, workload identity provider). Application secrets are
  write-only: names are listed, values are never displayed or re-requested.
- **Output encoding.** All server text is rendered as text. `dangerouslySetInnerHTML`
  is banned by lint. External links are shown as links only if they are
  `https:` without embedded credentials; otherwise they render as plain text.
  Post-login redirects accept only internal paths.
- **Error hygiene.** Server messages are shown only for 400/409/422, after
  dropping anything that looks like a stack trace, SQL, file path, internal
  host or credential. Deployment events, failure reasons and check details are
  passed through the same redaction. The console logger accepts only flat
  primitive context and redacts it; React's own error reporting is routed
  through it.
- **Safe retries.** Writes are never retried automatically. Creates,
  deployments and rollbacks send an `Idempotency-Key`; after a timeout the UI
  tells the user the outcome is unknown and offers a status check (a read),
  not a blind resubmit.
- **Truthful status.** Success is shown only after the backend confirms it;
  "accepted" and "completed" are distinct; progress is never invented.
- **Response validation.** Every response is decoded against the contract;
  unknown permission strings are ignored rather than granted.
- **Demo isolation.** Demo mode is chosen once at start-up from a build-time
  variable, is refused by `npm run build` in production mode, is removed from
  production bundles, labels every screen, and is never used as a fallback.

## What the backend must enforce

- Authentication on every endpoint; session cookies `HttpOnly`, `Secure`,
  `SameSite=Lax` or `Strict`; session rotation on login; logout invalidates
  the session server-side.
- CSRF validation for every unsafe method.
- Authorization on every request, including tenant isolation. Frontend
  permission checks are display logic only.
- Input validation for every field (the frontend's validation is for
  usability). In particular:
  - Repository URLs and image references must be validated server-side with
    SSRF protection (block private, link-local and metadata addresses after DNS
    resolution; restrict schemes and hosts).
  - Region, CPU, memory and instance limits must be checked against the
    provider and the organization's quotas.
- Idempotency-key handling for creates, deployments and rollbacks.
- Rate limiting, especially on login (with the same response for unknown
  users and wrong passwords).
- Error responses without stack traces, SQL, internal hosts or provider raw
  responses.
- Encrypting secret values at rest and never returning them, including in
  audit logs, deployment events and error messages.
- An append-only audit log written by the backend.
- CORS: if the API is on another origin, allow only the frontend origin with
  credentials; never `*`.

## What the deployment worker and cloud IAM must enforce

- AWS: the CloudBridge principal assumes customer roles with a unique,
  backend-generated external ID per connection; customer roles grant only the
  App Runner, ECR and `iam:PassRole` permissions needed, scoped to the
  resources CloudBridge manages.
- Google Cloud: workload identity federation bound to a dedicated service
  account with `roles/run.admin`, `roles/iam.serviceAccountUser` and
  `roles/artifactregistry.reader`, scoped to the project; no service account keys.
- The worker should run with its own least-privilege identity, separate from
  the API, and must not log credentials or secret values.
- Build isolation for repository sources (untrusted Dockerfiles run in
  sandboxed builders without access to CloudBridge credentials).

## Browser and hosting

`deploy/nginx.conf` serves the built frontend with a strict Content Security
Policy (`script-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`),
HSTS, `X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy`,
and proxies `/api/` to FastAPI so the API is same-origin. Serve only over
HTTPS. Do not add third-party scripts or analytics without revisiting the CSP.

## Known limitations

- The CSRF scheme relies on the API being same-site with the frontend (same
  origin behind the proxy, or `localhost` ports in development), because the
  frontend must read the `cb_csrf` cookie.
- Sign-in rate limiting is per API process (in memory). With several processes,
  add a shared limit at the reverse proxy.
- `VITE_*` variables are public. Never put secrets in them.
- Session expiry during an edit sends the user to sign in; unsaved form
  input is lost (by design, nothing is persisted in the browser).
