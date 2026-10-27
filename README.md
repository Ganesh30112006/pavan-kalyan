# CloudBridge

**Application Platform Abstraction Over Heterogeneous Cloud Infrastructure**

CloudBridge manages and deploys containerized applications across cloud
providers through one interface. The supported providers are **AWS App
Runner**, **AWS ECS Express Mode**, **Google Cloud Run**, **Azure Container
Apps** and **Kubernetes** (Azure AKS, or any cluster that trusts CloudBridge's
OIDC issuer).

This repository contains the web frontend (`frontend/`, React + TypeScript) and
the API (`backend/`, FastAPI + SQLAlchemy, PostgreSQL or SQLite). They share the
contract in [`docs/api-contract.md`](docs/api-contract.md). See
[`backend/README.md`](backend/README.md) for running the API, configuring cloud
access and how deployments work, and [`cli/README.md`](cli/README.md) for the
command-line client and GitHub Action.

## Features

- **Application shell**: sidebar (collapsible to an icon rail on desktop, a
  drawer on mobile) with route-synchronised highlighting; header with the
  organization, command search (Ctrl+K / ⌘K) over pages, actions and
  applications, a "needs attention" menu built from live failures, and an
  account menu.
- **Overview**: total and healthy applications, active and failed deployments
  (each linking to the filtered list), applications needing attention, a
  14-day deployment-outcome chart drawn only from real deployment records (with
  a table view), recent deployments and activity, connected providers and
  quick actions.
- **Applications**: search, health/status/provider filters, server-side
  sorting and pagination; type, runtime, deployment target, health (always in
  text) and serving version per row.
- **Create application wizard** (7 steps): details and type; source (Git
  repository with branch or immutable commit, Dockerfile path and build
  context, or a container image); runtime, port, health check, CPU and memory;
  scaling, request concurrency and public/private exposure; environment
  variables and secret references (with a warning against secrets in plain
  variables); deployment target with live **provider capability warnings**;
  review with per-section edit links.
- **Application details** with Overview (health, version, image digest,
  endpoint), Deployments, Configuration (requested vs effective provider
  configuration, backend validation, editing), Logs, Metrics (requests,
  errors, latency, instances, CPU, memory over 1 h–7 d), Domains (custom
  domains with the DNS records to create and their verification state) and
  Automation tabs; redeploy, pause/resume and typed-confirmation delete.
- **Automation**: auto-deploy on GitHub push (signed webhook, exact pushed
  commit), pull request preview applications that are deleted when the pull
  request closes, staging → production promotion of the exact image digest,
  approval gates for production, `cloudbridge.yaml` read from the repository,
  and sleep schedules that pause the app outside working hours.
- **Cost estimates** in the create wizard and on each application, from public
  list prices and clearly labelled as estimates.
- **Background workers**: application type "worker" deploys with no web
  address (no ingress or Service) and is verified by running state.
- **Deployments**: search, status (including "all in progress" / "all
  failures"), provider filter, sorting and pagination; details with stage
  timeline, events, build/rollout logs, sanitized failure details, endpoint,
  and rollback/cancel **only when the backend permits it**. All states are
  explicit: AWAITING_APPROVAL (approve/reject for owners and admins), QUEUED, VALIDATING, BUILDING, PUBLISHING, DEPLOYING, VERIFYING,
  SUCCEEDED, FAILED, ROLLING_BACK, ROLLED_BACK, FAILED_ROLLBACK (and CANCELLED).
- **Cloud providers**: one panel per provider (AWS App Runner, ECS Express
  Mode, Google Cloud Run, Azure Container Apps, Kubernetes) with connection status, account/project/subscription, region, last validation,
  permission-check result, supported capabilities, required permissions, and
  validate/connect/disconnect. AWS uses an IAM role with an external ID and
  Google Cloud uses workload identity federation, and Azure uses an app
  registration with a federated credential (AKS clusters reuse it): no
  long-lived keys, kubeconfigs or secrets.
- **Guided account setup**: `cloudbridge-connect.py`, served with the app, sets up the customer's side
  (IAM roles and policies for AWS; app registration, federated credential, role assignments and, if
  missing, resource group, Container Apps environment and registry for Azure; namespace access for
  AKS and OIDC clusters). The connection page shows the command with every value filled in; the
  customer runs it in AWS CloudShell or Azure Cloud Shell with their own sign-in. It shows each change
  and asks before making it (`--dry-run` only shows them), never creates keys or secrets, and sends
  nothing to CloudBridge. For Azure and AKS it runs before connecting and prints a link that fills in
  the form. Google Cloud keeps its manual steps.
- **Activity log**: search; actor, action, outcome, resource and date filters;
  request IDs. Kept separate from runtime logs and deployment events.
- **Settings**: profile and preferences, organization and team members,
  notification preferences, alert channels (Slack, Microsoft Teams, signed
  webhooks, with a test button), API tokens for the CLI and CI, current
  session, and platform information.
  Sections the backend does not support say so instead of disappearing.
- **Error and recovery pages**: sign-in, sign-out, access denied, not found,
  service unavailable, maintenance, and unexpected errors.

Every page handles loading, empty, no-results, error, stale-data, permission
and session-expiry states. See the "Frontend behaviour" section below.

## Repository layout

```
frontend/                React + TypeScript + Vite app
  src/
    app/                 App root, router, providers, route guards
    components/          Shared UI: layout, navigation, forms, feedback, dialogs, tables, status
    components/charts/   Accessible SVG charts (legend, tooltip, table view)
    pages/               One folder per area (auth, overview, applications, application-details,
                         deployments, providers, audit, settings, errors)
    services/api/        HTTP client, error model, response decoders, endpoints.ts (every path),
                         authApi, applicationsApi, deploymentsApi, providersApi, auditApi, settingsApi
    services/demo/       Development-only simulated backend (never in production builds)
    hooks/               Data loading, mutations, polling, unsaved-change protection
    types/               API and error types
    utils/               Formatting, validation, URL safety, redaction, messages
    styles/              Design tokens, base styles, layout, animations
  tests/                 Vitest + React Testing Library
backend/                 FastAPI service: routers, provider adapters (AWS App Runner, ECS Express, Google Cloud Run,
                         Azure Container Apps, Kubernetes), background worker, Alembic migrations, tests
frontend/public/cloudbridge-connect.py   Guided cloud account setup script (stdlib only, Python 3.9+)
cli/                     Single-file CLI (stdlib only) and a composite GitHub Action
docs/                    API contract and security notes
deploy/nginx.conf        Example production server with security headers
```

## Getting started

Requirements: Node.js 22.13 or newer, Python 3.11 or newer.

Start the API first (details in [`backend/README.md`](backend/README.md)):

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # macOS/Linux: .venv/bin/activate
pip install -r requirements-dev.txt && cp .env.example .env
python -m app.cli migrate
python -m app.cli create-user --email you@example.com --name "You" --role owner --new-organization "Acme"
uvicorn app.main:create_app --factory --reload --port 8000
```

Then the frontend:

```bash
cd frontend
npm ci
cp .env.example .env.local      # then set VITE_API_BASE_URL if needed
npm run dev                     # http://localhost:5173, talks to the real backend
```

### Demo mode (development only)

To work on the UI without a backend:

```bash
npm run dev:demo
```

This loads `.env.demo` (`VITE_DEMO_MODE=true`) and serves simulated data:

- Every screen shows a **DEMO MODE** banner, and notifications say the result
  was simulated. No cloud provider is contacted.
- Any email and password sign in. The simulated state resets on page reload.
- Demo mode is chosen once at start-up. The app never switches to demo data
  because a real request failed.
- `npm run build` (production mode) **fails** if `VITE_DEMO_MODE=true`, and the
  demo module is removed from production bundles (CI checks both). A demo
  preview can be built explicitly with `npx vite build --mode demo`. Never deploy
  that build as the real product.

### Environment variables

| Variable            | Default | Purpose |
| ------------------- | ------- | ------- |
| `VITE_API_BASE_URL` | empty (same origin) | Backend base URL, e.g. `http://localhost:8000` in development. Leave empty in production when `/api/` is proxied on the same origin. |
| `VITE_DEMO_MODE`    | `false` | Development-only simulated backend (see above). |
| `VITE_SUPPORT_URL`  | empty   | Optional `https://` link shown on error pages as "Contact support". |

All `VITE_*` values are compiled into public JavaScript. **Never put secrets in
them.**

For local development against FastAPI on another port, the backend must allow
CORS from `http://localhost:5173` with credentials, and set its cookies for
`localhost`.

## Scripts

Run inside `frontend/`:

| Command             | What it does |
| ------------------- | ------------ |
| `npm run dev`       | Development server against the real API |
| `npm run dev:demo`  | Development server with simulated data |
| `npm run typecheck` | TypeScript strict type checking |
| `npm run lint`      | ESLint (including bans on `dangerouslySetInnerHTML` and browser storage) |
| `npm test`          | Unit and component tests |
| `npm run build`     | Type check and production build into `dist/` |
| `npm run preview`   | Serve the production build locally |

## Deployment

1. Build with `VITE_DEMO_MODE` unset: `npm ci && npm run build`.
2. Serve `frontend/dist` over HTTPS. `deploy/nginx.conf` is a working example:
   it falls back to `index.html` for client-side routes, proxies `/api/` to
   FastAPI on the same origin, caches hashed assets, hides source maps, and
   sets a strict Content Security Policy, HSTS and other security headers.
3. If the API is on a different origin, set `VITE_API_BASE_URL` at build
   time, add that origin to the CSP `connect-src`, and configure CORS with
   credentials for the frontend origin only.

## Frontend behaviour

- **Loading**: skeletons shaped like the content; background refreshes keep
  the data on screen and show "Refreshing…".
- **Errors**: a central error model (`services/api/errors.ts`) maps network
  failures, timeouts, malformed responses and every HTTP status to a title,
  explanation and recovery action, and shows the request ID. Server text is
  shown only for request errors and only after sanitising.
- **Stale data**: when a refresh fails, the last data stays visible with a
  warning.
- **Writes**: buttons show a spinner and prevent duplicate submissions;
  nothing state-changing is retried automatically; after a timeout the UI
  says the outcome is unknown and offers a status check.
- **Async work**: "accepted" and "completed" are separate messages; progress
  follows what the backend reports and is never estimated; polling is bounded,
  pauses in hidden tabs, and can be resumed.
- **Forms**: labels, required and optional markers, client validation,
  server field errors mapped back to fields, input kept on recoverable errors,
  and a prompt before leaving with unsaved changes.
- **Accessibility**: semantic landmarks, skip link, visible focus, keyboard-
  operable dialogs and menus with focus restoration, one polite live region for
  state changes, text labels on every status, and reduced-motion support.
- **Responsive**: a sidebar becomes a drawer below 56rem; tables become
  labelled blocks below 40rem.

## Testing

```bash
cd frontend
npm run typecheck && npm run lint && npm test && npm run build
```

Tests use a fake API (`tests/helpers/fakeApi.ts`) and mocked `fetch`; they
never contact a real backend or cloud provider. Coverage includes every
route, loading, empty and error states, HTTP status handling, malformed
responses, form validation and server error mapping, session expiry,
permissions, duplicate submissions, idempotency keys, connection and
deployment progress, polling limits, confirmation dialogs, demo-mode
isolation, secret redaction, provider capability warnings, backend-gated rollback, endpoint paths, command search, sorting and filtering, keyboard interaction, reduced motion, the mobile
navigation and the error boundary.

## Features not in the API yet

The backend implements every endpoint in [`docs/api-contract.md`](docs/api-contract.md),
including runtime and deployment logs and cancellation (offered until
provisioning starts). Features with no endpoint are shown as unavailable rather
than simulated: listing or revoking other sessions, switching organizations,
inviting members and changing roles (use `python -m app.cli create-user`), and
a notification feed (the header uses live failures instead). Notification preferences appear only when the backend has SMTP configured.

## Security

See [`docs/security.md`](docs/security.md) for what the frontend does and,
more importantly, what FastAPI, the deployment worker and cloud IAM must
enforce. The frontend alone does not make the platform secure.
