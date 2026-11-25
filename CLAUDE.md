# CloudBridge: handover for Claude sessions

Read this first. It records what the project is, how it works, the rules the owner set, the state of
the cloud accounts, and the traps already hit. Details live in `README.md`, `backend/README.md`,
`docs/api-contract.md`, `docs/security.md` and `cli/README.md`; this file points to them rather than
repeating them.

## 1. Goal

CloudBridge is an application platform over several clouds: a user describes a containerized app once
(source, runtime, port, CPU/memory, scaling, env vars, secret references) and CloudBridge deploys and
manages it on the provider they pick, with one UI, API and CLI. Supported providers (`ProviderKind`):

| Kind | Service | How CloudBridge signs in (never long-lived customer keys) |
|---|---|---|
| `aws_app_runner` | AWS App Runner | STS AssumeRole into a customer IAM role, per-connection external ID |
| `aws_ecs_express` | ECS Express Mode | Same role model as App Runner, ECS roles instead of App Runner ones |
| `azure_container_apps` | Azure Container Apps | Federated credential on the customer's Entra app registration (no client secret) |
| `kubernetes` | AKS (`auth: aks`) or any cluster (`auth: oidc`) | AKS reuses the Azure federation; OIDC clusters trust CloudBridge's token |
| `gcp_cloud_run` | Google Cloud Run | Workload identity federation + service account impersonation |

The product promise, enforced everywhere: **status always comes from the provider, nothing is
simulated, secrets are never shown, logged or stored in plain form, and no customer passes CloudBridge a
permanent credential.**

## 2. Owner's standing rules (follow without being reminded)

- **Never use Google Cloud** (`gcloud`, GCP APIs, live GCP tests). GCP code exists but stays untested live.
- **Don't destroy anything you didn't create.** Before deleting, check it was made by the test.
- **Delete everything a test creates** once that test is done (cloud resources, local DBs, browser
  files, profiles), then verify it's gone.
- **No bugs**: careful code, null-safe parsing of provider responses (`(x.get("k") or {})`, never
  `.get("k", {})` for nested dicts), regression test per fix, mutation-check new tests, run all checks.
- **Quiet progress, one overview at the end** (short status lines while working are fine; no essays).
- Spec rules: never fake cloud success; no secrets in `VITE_*`; never collect/expose/store permanent
  cloud credentials; never render or log secret values; avoid `any` in TypeScript; don't replace
  working code with scaffolds; **don't run Prettier on the frontend**.
- Ask before anything outward-facing or hard to undo (creating IAM identities, deploying, deleting
  shared things). The user wants to *watch* live tests: open visible windows/browser, let them answer.

## 3. Repository map

```
frontend/              React 19 + TS + Vite + React Router 7; vitest + Testing Library
  src/services/api/    HTTP client, error model (errors.ts), runtime decoders (decoders.ts), endpoints.ts
  src/services/demo/   Simulated backend for `npm run dev:demo` only (refused in production builds)
  src/pages/providers/ Connect form, connection page, setup-script panel (ScriptSetup.tsx, setupCommand.ts)
  public/cloudbridge-connect.py   Customer-side guided setup script (served at /cloudbridge-connect.py)
backend/               FastAPI + SQLAlchemy 2 + Alembic (SQLite dev, PostgreSQL prod), Python 3.11+
  app/routers/         auth, applications, deployments, providers, audit, settings, platform, hooks, alerts, tokens
  app/providers/       base.py (adapter interface), aws.py, ecs.py, azure.py, kubernetes.py, gcp.py, entra.py,
                       capabilities.py, images.py
  app/worker/          deployment state machine (deployments.py), validation, deletion, health, schedules, alerts
  app/services/        specs validation, idempotency, rate limit, audit, costs, cloudbridge.yaml, alerts
  tests/               332 tests; botocore Stubber / httpx MockTransport; no network (plus API fuzzing,
                       hypothesis properties, cross-org/viewer sweeps over every route)
cli/cloudbridge.py     Stdlib-only CLI + GitHub Action (cli/action.yml), uses API tokens
docs/                  api-contract.md (source of truth for every response shape), security.md, deployment.md (operators)
deploy/nginx.conf      Production example (same-origin /api proxy, strict CSP)
deploy/compose.yaml    Docker Compose stack: PostgreSQL, migrate, API, worker, web (frontend/Dockerfile + nginx/ template)
.github/workflows/ci.yml  ruff, ruff format, mypy, pytest; typecheck, lint, test, build, demo-isolation checks
```

## 4. Core logic

**Tenancy and roles.** Every row belongs to an organization; every query filters by it. Roles →
permissions (`backend/app/domain.py`): owner = all; admin = all but `organization:manage`; developer =
create/update apps, deploy, cancel, rollback, read audit; viewer = read only. `providers:manage` is
needed to add/validate/delete connections and alert channels. Frontend checks are display only.

**Auth.** HttpOnly session cookie + double-submit CSRF (`cb_csrf` cookie → `X-CSRF-Token`). API tokens
(`cbt_…`) for the CLI/CI. Login is rate limited with identical responses for unknown user / bad password.

**Connections** (`routers/providers.py`, `api/serializers.py`). Statuses: `pending_validation →
validating → connected` or `invalid_credentials | insufficient_permissions | provider_unavailable |
timed_out | error`. Validation runs in the worker and reports named checks (AWS: assume role, App
Runner permissions, ECR access, account matches, CodeBuild). Each connection's `setup` tells the
customer what to trust: AWS `external_id` (server-generated `cb-<32 hex>`, never client-chosen),
`trusted_principal` (`CLOUDBRIDGE_AWS_PRINCIPAL_ARN`), Azure/AKS `federation_issuer` + subject +
audience `api://AzureADTokenExchange`, and `aws` (role names + ECR prefix from settings).
`GET /providers/{provider}/setup` gives the same before a connection exists (Azure/AKS need it).

**CloudBridge's own identity** (operator side, set once): AWS role/user in `CLOUDBRIDGE_AWS_PRINCIPAL_ARN`;
Azure and AKS via AWS outbound web identity federation (`sts:GetWebIdentityToken`) exchanged at Entra
(`CLOUDBRIDGE_AZURE_TOKEN_SOURCE=aws`, issuer/subject settings); GCP via STS exchange.

**Guided setup script** (`frontend/public/cloudbridge-connect.py`, Python 3.9+, stdlib only). Runs in the
customer's AWS CloudShell / Azure Cloud Shell with *their* sign-in; shows every step and asks y/n/q;
`--dry-run`; never creates keys or secrets; sends nothing to CloudBridge. Subcommands:
The script prints every step with its details, then asks once (`y` all, `s` step by step, `q` quit).
- `aws`: role CloudBridge assumes (trust = principal + external ID), inline policy `CloudBridge`, helper
  roles (`CloudBridgeAppRunnerECRAccess`, `CloudBridgeAppRunnerInstance` or ECS execution/infrastructure,
  `CloudBridgeCodeBuild`). Stops if signed in to another account or a same-name role has another path.
- `azure`: providers registration, resource group, Container Apps env, optional ACR, app registration +
  service principal + federated credential, role assignments, optional `cloudbridge-runtime` identity;
  prints a `/providers/new?...` link that pre-fills the connect form.
- `aks`: finds the cluster (by name or API server), requires Entra ID + Azure RBAC, optional namespace
  create via `az aks command invoke`, AKS RBAC Writer on the namespace, prints API server + public CA.
- `kubernetes`: namespace Role/RoleBinding via kubectl, prints the JWT authenticator config.
- `cloudformation [--ecs]`: prints the same AWS roles/policies as a CloudFormation template (built from the same
  functions; a test proves they match). Operators host it on S3 and set `CLOUDBRIDGE_AWS_[ECS_]CLOUDFORMATION_TEMPLATE_URL`;
  the connection page then shows a "Launch Stack" quick-create link (`launchStackUrl`) above the script.
The UI builds the exact command (`setupCommand.ts`); for Azure/AKS before saving, after saving for AWS.
AWS validation retries the AssumeRole after 5s and 10s when it is refused (a just-created role propagating).
`backend/tests/test_connect_script.py` checks the AWS policy covers every SDK call in aws.py/ecs.py —
**when an adapter starts calling a new AWS API, update the script's policy, ProviderPermissions.tsx and
backend/README.md together.**

**Deployments** (`worker/deployments.py`): `(awaiting_approval) → queued → validating → (building) →
publishing → deploying → verifying → succeeded`, else `failed` / `rolling_back → rolled_back |
failed_rollback` / `cancelled`. Builds: CodeBuild / ACR Tasks / Cloud Build (repository sources only).
Publishing pins the image to its `sha256` digest. Verification needs provider "running" plus a 2xx/3xx
health check on provider-issued hosts only. Failed verification restores the previous version.
Every step is persisted with leases, so a restarted worker resumes. Triggers: manual, API/CLI, GitHub
push (signed webhook), PR previews, promotion (exact digest), with optional approval gate.

**Validation.** `services/specs.py` + `app/validation.py` check every field server-side (SSRF-safe
repository/image hosts, provider limits, regions; Kubernetes region is always `in-cluster`). Plain env
vars that look like credentials (AWS keys, PEM, GitHub/Slack/Stripe tokens, `AccountKey=`, service
account JSON) are refused with 422 on `environment.N.value`. Errors use one envelope
(`ErrorEnvelope`), 405 sets `Allow`, API responses carry `default-src 'none'` CSP.

**Frontend conventions.** Every response is decoded against the contract; errors go through
`describeError`; writes are never auto-retried and send `Idempotency-Key`; uncertain outcomes offer a
"Check status" read; polling is bounded; server field errors show on the field and in the summary
(`fieldErrorFor`, `FormErrorSummary listServerFieldErrors`). Accessibility is tested with axe at
1280/375/320 px: links in text are underlined, scrollable regions are focusable (`TableScroll`) or wrap
(`.code-block--wrap`), notices sit in landmarks.

## 5. Commands

```bash
# backend (Git Bash on Windows: .venv/Scripts/...)
cd backend && .venv/Scripts/python -m pytest -q
.venv/Scripts/ruff check app tests && .venv/Scripts/ruff format --check app tests && .venv/Scripts/mypy app tests
# the setup script is linted separately (targets Python 3.9)
.venv/Scripts/ruff check ../frontend/public/cloudbridge-connect.py --target-version py39 --line-length 120
.venv/Scripts/mypy --strict ../frontend/public/cloudbridge-connect.py
# frontend
cd frontend && npx tsc -p tsconfig.app.json --noEmit && npx eslint . && npx vitest run && npm run build
```

Local run: backend on `localhost:8000` (`uvicorn app.main:create_app --factory`), frontend on
`localhost:5173` with `VITE_API_BASE_URL=http://localhost:8000`, `CLOUDBRIDGE_CORS_ORIGINS=http://localhost:5173`,
`CLOUDBRIDGE_COOKIE_SECURE=false`. Use `localhost` for both (cookies). Create a user with
`python -m app.cli create-user ... --new-organization ...` (`CLOUDBRIDGE_BOOTSTRAP_PASSWORD` for
non-interactive). Demo UI without backend: `npm run dev:demo` (in-memory, page reload logs out; navigate
with history.pushState in Playwright to keep the session).

Current baseline (2026-11-25): backend 332 tests (312 of them also green on PostgreSQL 16 on 2026-11-24), frontend 278 tests, all
lint/type/build checks clean, `npm audit` 0. PostgreSQL run: start a throwaway `postgres:16-alpine` container and
set `CLOUDBRIDGE_TEST_DATABASE_URL=postgresql+psycopg://...` (each test resets the schema; CI job `backend-postgres`).
Fuzz deeper locally with `CLOUDBRIDGE_FUZZ_ROUNDS=40 CLOUDBRIDGE_FUZZ_SEED=<n>`.

## 6. Cloud accounts and their state

- **AWS**: account `492462083767`, default region `ap-south-1`. CLI default profile signs in as the
  **root user** via `aws login` (short-lived). Profiles: `default`, `control-plane`. No CloudBridge
  resources remain (last live test fully cleaned 2026-10-18).
- **Azure**: subscription "Azure for Students" `9dd19c8e-c389-44d5-86f6-4f5f07322066`, tenant
  `808cc83e-a546-47e7-a03f-73a1ebba24f3`, `az` signed in. No resource groups remain. Student
  subscriptions don't allow ACR Tasks (builds fail there; image sources work).
- **Kubernetes**: local `kubectl` context `docker-desktop` only; the AKS test cluster was deleted.
- **GCP**: not used, by rule.

Live results so far: App Runner, ECS Express, Azure Container Apps and AKS all passed end to end
(earlier sessions); on 2026-10-18 the setup script + connect + validate + App Runner deploy + delete
passed on the real AWS account. On 2026-11-16 the shorter AWS connect flow (role ARN filled from the
account ID, one-paste `curl … && python3 …` command, a single confirmation for the whole plan, "Validate now" under
the script step) passed live: Connected on the first validation; everything cleaned.
On 2026-11-25 (unattended, user asleep): App Runner + ECS connections Connected first time; the sample app
("Use the sample app" → `examples/hello-web`, built by CodeBuild) ran on ECS Express; config change → r2, rollback,
pause/resume, logs, metrics, API token + CLI `apply`/`deploy --wait`, image source, staging → production promotion
with approval (same digest), deletes; all cleaned. **App Runner now refuses new services in this account** ("Account …
can't create new App Runner services"); use ECS Express. Two bugs found and fixed: that error now suggests ECS Express,
and deleting an app whose only deployment failed before the service existed skipped the ECR/CodeBuild cleanup. Shared report artifact: https://claude.ai/artifact/718QogzEMgpRuzs2shY9xg

### Production deployment of CloudBridge itself (since 2026-11-25)

Live at https://cl-becd21485cb8479ca542816a73b83139.ecs.ap-south-1.on.aws (first owner `owner@cloudbridge.example`, password in Secrets Manager
`cloudbridge/production/owner-password`). Every push to `main` that passes CI is deployed by the `deploy` job (GitHub OIDC → role `cloudbridge-github-deploy`,
image `cloudbridge-app` in ECR, ECS Express service `cloudbridge` in cluster `default`, ap-south-1). Single container
(`deploy/aws/Dockerfile`: API serves the web app via `CLOUDBRIDGE_STATIC_DIR`, in-process worker), settings in
`deploy/aws/service.json`, RDS `cloudbridge-prod` (deletion protection on), secrets `cloudbridge/production/*`. These
are **long-lived resources the owner asked for: never delete them in test cleanups** (the cleanup rule covers only
what a test creates). Details: docs/deployment.md, "AWS with CI/CD". A PreToolUse hook blocks any Bash command that
mentions reading a secret value; read secrets only in the console or let ECS inject them.

### Running a live AWS test (keyless recipe that works)

Root cannot assume roles, and the safety classifier blocks creating access keys. So:
1. Ask the user first. Create IAM user `cloudbridge-platform-test` with only `sts:AssumeRole` on
   `arn:aws:iam::492462083767:role/CloudBridgeDeploy` + managed policy `SignInLocalDevelopmentAccess`.
2. The user sets its console password, then runs `aws login --profile cloudbridge-platform --remote`
   and opens the link in a **private window** (a root browser session is silently reused otherwise).
3. Start the backend with `AWS_PROFILE=cloudbridge-platform` and
   `CLOUDBRIDGE_AWS_PRINCIPAL_ARN=arn:aws:iam::492462083767:user/cloudbridge-platform-test`.
4. Create the connection in the UI, open the setup script in a visible PowerShell window for the user
   to answer, validate, deploy something tiny (e.g. `public.ecr.aws/nginx/nginx:stable`, 0.25 vCPU /
   512Mi, max 1 instance), delete it through CloudBridge.
5. Clean up: CloudBridge* roles (remove inline/attached policies first), `AWSServiceRoleForAppRunner`
   (only if the test created it), App Runner scaling configs `cloudbridge-*` and log groups
   `/aws/apprunner/<app>/...`, the user (login profile, policies), the profile in `~/.aws/config`
   (`aws logout --profile ...` then remove the section), local DB/servers/`.playwright-mcp`.

## 7. Known gaps / open decisions

- OpenAPI schema is looser than the semantic validation (422 always names the field).
- App deletion leaves App Runner log groups and the shared `cloudbridge-*` scaling config (by design);
  owner was offered optional log-group cleanup, not yet decided.
- Azure and GCP built images are not removed on app delete. Domains tab never exercised live.
- GCP adapter never live-tested (by rule).
- ECS Express app deletion leaves the task-definition family (`default-<app>`) and `/aws/ecs/default/<app>-*` log
  groups, plus CodeBuild log groups; the live test removed them by hand. The ECS "service deleted outside
  CloudBridge" fix is unit-tested only: the auto-mode classifier blocked deleting the service by hand.
- Launch Stack passed live on 2026-11-16 (App Runner template in a private S3 bucket, stack created with exactly
  the link's template URL and parameters, CREATE_COMPLETE, then Connected on the first validation; all deleted).
  The ECS template passed the same way (CREATE_COMPLETE, Connected). The console's quick-create page itself was
  not clicked (needs a root console sign-in nobody was there to do).

## 8. Environment traps (Windows + Git Bash + PowerShell)

- Editing files from Python: use `read_bytes`/`write_bytes`. `write_text` converts to CRLF; the repo is
  LF and a CR after the script's shebang breaks it on Linux (a test guards the script).
- Python heredocs run through the Bash tool collapse `\\` → `\`; use the Edit tool for lines with escapes.
- Git Bash rewrites `/aws/...` arguments into Windows paths: prefix `MSYS_NO_PATHCONV=1`. AWS CLI output
  ends lines with `\r`: pipe through `tr -d '\r'`. JSON for AWS CLI: `file://$(cygpath -w path)`.
- Opening a window for the user: `Start-Process powershell -ArgumentList '-NoExit','-EncodedCommand',$b64`
  (plain `-Command` strings lose quotes and break on parentheses).
- Playwright MCP can only write inside the project folder; delete `.playwright-mcp/` afterwards.
- `aws login` on Windows ignores `BROWSER` and also opens the user's default browser, where root is signed
  in: if that tab completes first, the test profile gets **root**. Check `aws sts get-caller-identity --profile …`
  after every login; on root, delete the profile's `login_session` line (never `aws logout` a root session) and redo.
- Docker Desktop is usually not running; no Python 3.9 locally (use `vermin -t=3.9-` to check the script).
  `docker desktop stop` shuts it down again after a test.
- Migrations must run on PostgreSQL too: boolean `server_default` needs `false` there (`0` broke 0003 until
  2026-11-24). Parse URLs with `app.urls.split_url` (urlsplit raises on `https://[::1`), and alert webhooks go
  through `PublicOnlyTransport` (IP pinned after the public-address check, against DNS rebinding).
- GitHub OIDC tokens now use subjects with immutable IDs (`repo:Owner@<id>/repo@<id>:ref:refs/heads/main`); an IAM
  trust on the old `repo:Owner/repo:ref:…` form is refused. CloudTrail `AssumeRoleWithWebIdentity` shows the real subject.
- Production operation (2026-11-25): `/api/v1/health` = liveness, `/api/v1/ready` = readiness (DB, schema vs Alembic
  heads, in-process worker). Schema `newer` passes on purpose (rolling upgrades), so **migrations must stay additive
  while two releases overlap**. The web nginx resolves the API name at request time (`resolver`, variable
  `proxy_pass`); a fixed `proxy_pass http://api:8000` kept a recreated API's old IP and returned 502.
  Chunked bodies over the limit used to give 500 (or 400 from FastAPI); `BodySizeLimit` now answers 413.
