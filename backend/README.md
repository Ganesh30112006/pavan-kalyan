# CloudBridge API (FastAPI)

The backend for the CloudBridge web app. It implements
[`docs/api-contract.md`](../docs/api-contract.md) exactly; the test suite checks every response
shape against that contract, and the frontend decodes every response at runtime.

- **FastAPI + SQLAlchemy 2 + Alembic.** SQLite for development, PostgreSQL for production.
- **Cookie sessions with CSRF protection** (double-submit `cb_csrf` cookie + `X-CSRF-Token` header).
- **Real provider integrations**: AWS App Runner (STS AssumeRole with a per-connection external ID,
  ECR, CodeBuild, CloudWatch Logs), Google Cloud Run (workload identity federation, Cloud Build,
  Artifact Registry, Cloud Run Admin API v2, Cloud Logging) and Azure Container Apps (federated
  credential on an Entra ID app registration, ACR Tasks, Azure Resource Manager, Log Analytics), AWS
  ECS Express Mode (same role setup as App Runner) and Kubernetes (AKS through the Azure federated
  identity, or any cluster that trusts CloudBridge's OIDC issuer). No customer access keys, service
  account keys, kubeconfigs or client secrets are accepted or stored.
- **Platform features**: auto-deploy on GitHub push (signed webhooks), pull request preview
  applications, staging → production promotion of the exact image digest, approval gates,
  `cloudbridge.yaml` configuration in the repository, sleep schedules and pause/resume, metrics,
  cost estimates, custom domains, background workers, Slack/Teams/signed-webhook alerts, and API
  tokens for the [CLI and GitHub Action](../cli/README.md).
- **A background worker** runs deployments, connection validation, deletions and health checks. It
  persists every step, so a restart resumes where it stopped. Leases make it safe to run several
  worker processes.
- **Status always comes from the provider.** A deployment succeeds only after the provider confirms
  the rollout and the service passes verification. Without working platform credentials, validation
  and deployments fail with a clear reason; nothing is simulated.

## Quick start (development)

Requirements: Python 3.11+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash: source .venv/Scripts/activate)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env              # development defaults: SQLite, CORS for http://localhost:5173

python -m app.cli migrate
python -m app.cli create-user --email you@example.com --name "Your Name" --role owner --new-organization "Acme"
uvicorn app.main:create_app --factory --reload --port 8000
```

Then, in `frontend/`, set `VITE_API_BASE_URL=http://localhost:8000` in `.env.local` and run
`npm run dev`. Open **http://localhost:5173** (use `localhost`, not `127.0.0.1`, for both so the
cookies are shared).

Interactive API docs: http://localhost:8000/api/v1/docs (disabled in production by default).

## Commands

| Command | What it does |
| ------- | ------------ |
| `python -m app.cli migrate` | Create or upgrade the database schema |
| `python -m app.cli create-user --email … --name … --role owner\|admin\|developer\|viewer (--new-organization NAME \| --organization-id ID)` | Create a user (password is prompted, or read from `CLOUDBRIDGE_BOOTSTRAP_PASSWORD`) |
| `python -m app.cli set-password --email …` | Reset a password |
| `uvicorn app.main:create_app --factory` | Run the API (with the worker, unless `CLOUDBRIDGE_RUN_WORKER=false`) |
| `python -m app.worker` | Run the worker as a separate process |
| `pytest` | Tests (201) |
| `ruff check app tests && ruff format --check app tests` | Lint and formatting |
| `mypy app tests` | Strict type checking |

## Configuration

All settings are `CLOUDBRIDGE_*` environment variables (see `app/config.py` and `.env.example`).

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `ENVIRONMENT` | `development` | `production` enforces secure cookies and hides API docs |
| `DATABASE_URL` | `sqlite:///./cloudbridge.db` | e.g. `postgresql+psycopg://user:pass@host/db` |
| `CORS_ORIGINS` | empty | Comma-separated frontend origins, only when the API is on another origin |
| `COOKIE_SECURE` | `true` | Set `false` only for plain-HTTP localhost |
| `RUN_WORKER` | `true` | Run the worker inside the API process |
| `AWS_PRINCIPAL_ARN` | empty | CloudBridge's own AWS role, shown to users as the principal to trust |
| `AWS_REGIONS` / `GCP_REGIONS` / `AZURE_REGIONS` | see config | Regions offered and accepted |
| `GCP_SUBJECT_TOKEN_SOURCE` | `none` | `aws` (use CloudBridge's AWS identity) or `file` (OIDC token file) |
| `GCP_TRUSTED_PRINCIPAL` | empty | Shown to users as the identity their workload identity pool must trust |
| `AZURE_TOKEN_SOURCE` | `none` | `aws` (`sts:GetWebIdentityToken`) or `file` (OIDC token file, `AZURE_TOKEN_FILE`) |
| `AZURE_FEDERATION_ISSUER` / `AZURE_FEDERATION_SUBJECT` | empty | Shown to users as the issuer and subject their federated credential must trust |
| `AWS_ECS_EXECUTION_ROLE_NAME` / `AWS_ECS_INFRASTRUCTURE_ROLE_NAME` | `CloudBridgeECSExecution` / `CloudBridgeECSInfrastructure` | Roles ECS Express services use in customer accounts |
| `KUBERNETES_AKS_SCOPE` | AKS server app | Entra scope requested for AKS clusters |
| `ALLOW_PRIVATE_ALERT_URLS` | `false` | Development only (refused in production): allow alert webhooks on private addresses |
| `PUBLIC_URL` | empty | Links in e-mails and alerts |
| `SMTP_HOST`, `SMTP_FROM`, … | empty | E-mail notifications; notification settings appear only when set |
| `MAINTENANCE_MODE` | `false` | Answer every API call with 503 `maintenance` |

## Cloud setup

Customers don't have to do the steps below by hand: the connection page gives them a command for
`frontend/public/cloudbridge-connect.py`, which they run in AWS CloudShell or Azure Cloud Shell. It
does everything under "In each customer AWS account / Azure subscription" and "Kubernetes clusters"
with their own sign-in, asking before each change. It takes the role names and ECR prefix from
`setup.aws` in the API, so keep the `CLOUDBRIDGE_AWS_*_ROLE_NAME` settings and the script in step;
`tests/test_connect_script.py` checks that its IAM policy allows every AWS call the adapters make.
CloudBridge's own identity (next section) is still set up once by whoever runs CloudBridge.

### CloudBridge's own identity

- **AWS**: run CloudBridge with an IAM role (instance profile, ECS task role, or `AWS_PROFILE` in
  development) and set `CLOUDBRIDGE_AWS_PRINCIPAL_ARN` to that role's ARN. Customer roles trust it,
  with the external ID shown on each connection.
  - **Local development without access keys**: the root user cannot assume roles, so use an IAM user
    whose only permissions are `sts:AssumeRole` on the customer role(s) and the AWS managed policy
    `SignInLocalDevelopmentAccess` (needed by `aws login`). Give it a console password, run
    `aws login --profile cloudbridge-platform` (add `--remote` and open the link in a private window if
    your browser is signed in as root, otherwise it silently reuses the root session), then start
    CloudBridge with `AWS_PROFILE=cloudbridge-platform` and `CLOUDBRIDGE_AWS_PRINCIPAL_ARN` set to the
    user's ARN. Credentials are short-lived; `aws logout --profile cloudbridge-platform` ends them.
- **Google Cloud**: set `CLOUDBRIDGE_GCP_SUBJECT_TOKEN_SOURCE=aws` to exchange CloudBridge's AWS
  identity at Google STS through the customer's workload identity provider (an AWS provider in their
  pool), or `file` with `CLOUDBRIDGE_GCP_SUBJECT_TOKEN_FILE` for an OIDC token (e.g. Kubernetes
  projected service account token). CloudBridge then impersonates the customer's service account.
- **Azure**: set `CLOUDBRIDGE_AZURE_TOKEN_SOURCE=aws` and enable IAM outbound identity federation in
  CloudBridge's AWS account (`aws iam enable-outbound-web-identity-federation`); give CloudBridge's role
  `sts:GetWebIdentityToken`. Set `CLOUDBRIDGE_AZURE_FEDERATION_ISSUER` to the account's issuer
  (`aws iam get-outbound-web-identity-federation-info`) and `CLOUDBRIDGE_AZURE_FEDERATION_SUBJECT` to the
  role's ARN. CloudBridge exchanges that signed token at Microsoft Entra ID for a token of the customer's
  app registration. Or use `file` with `CLOUDBRIDGE_AZURE_TOKEN_FILE` for another OIDC issuer.

### In each customer AWS account

1. The connection role, trusted by `CLOUDBRIDGE_AWS_PRINCIPAL_ARN` with the connection's external ID,
   with the permissions listed in the UI (App Runner, ECR, CodeBuild, CloudWatch Logs, `iam:PassRole`, and
   `iam:CreateServiceLinkedRole` limited to `apprunner.amazonaws.com`, which App Runner needs for the first
   service in an account). `apprunner:DeleteAutoScalingConfiguration` lets CloudBridge free unused
   `cloudbridge-*` scaling configurations when the account reaches App Runner's limit of 10 per region.
   When an application is deleted, CloudBridge removes the `cloudbridge/<name>` ECR repository and
   `cloudbridge-<name>` CodeBuild project it created for it (only if tagged with that application's ID), which
   needs `ecr:ListTagsForResource`, `ecr:DeleteRepository`, `codebuild:BatchGetProjects` and
   `codebuild:DeleteProject`; without them they are left in place and the deletion still succeeds.
   Custom domains and pausing need `apprunner:AssociateCustomDomain`, `DescribeCustomDomains`,
   `DisassociateCustomDomain`, `PauseService` and `ResumeService`; metrics need `cloudwatch:GetMetricData`.
   For ECS Express Mode the role also needs `ecs:CreateExpressGatewayService`, `UpdateExpressGatewayService`,
   `DescribeExpressGatewayService`, `DeleteExpressGatewayService`, `CreateCluster` (Express Mode creates the
   `default` cluster on first use), `RegisterTaskDefinition`, `DeregisterTaskDefinition`,
   `DescribeTaskDefinition`, `CreateService`, `UpdateService`, `DeleteService`, `DescribeServices`,
   `ListServices`, `DescribeServiceDeployments`, `ListServiceDeployments`, `ListClusters`, `DescribeClusters`,
   `TagResource`, `UntagResource`, `ListTagsForResource`, and `iam:CreateServiceLinkedRole` for
   `ecs.amazonaws.com` and `elasticloadbalancing.amazonaws.com`.
2. `CloudBridgeAppRunnerECRAccess`: trusted by `build.apprunner.amazonaws.com`, with
   `AWSAppRunnerServicePolicyForECRAccess` (pulls private ECR images).
3. `CloudBridgeAppRunnerInstance`: trusted by `tasks.apprunner.amazonaws.com`, allowed to read the
   referenced Secrets Manager secrets / SSM parameters (needed only with secret references).
4. `CloudBridgeCodeBuild`: trusted by `codebuild.amazonaws.com`, allowed to push to
   `cloudbridge/*` ECR repositories and write CloudWatch Logs (repository sources only).

5. ECS Express only: `CloudBridgeECSExecution` (trusted by `ecs-tasks.amazonaws.com`, with
   `AmazonECSTaskExecutionRolePolicy`, plus read access to referenced secrets) and
   `CloudBridgeECSInfrastructure` (trusted by `ecs.amazonaws.com`, with
   `AmazonECSInfrastructureRoleforExpressGatewayServices`). The connection role may pass both.

Role names can be changed with `CLOUDBRIDGE_AWS_APPRUNNER_ACCESS_ROLE_NAME`,
`…_INSTANCE_ROLE_NAME`, `CLOUDBRIDGE_AWS_CODEBUILD_ROLE_NAME` and the `…_ECS_…_ROLE_NAME` settings.

### In each customer Google Cloud project

Enable the Cloud Run Admin, Artifact Registry, Cloud Build and Cloud Logging APIs; create the
workload identity pool and provider trusting CloudBridge; grant CloudBridge's federated identity
`roles/iam.workloadIdentityUser` on the deployer service account; give that service account the
roles listed in the UI. The runtime service account needs `roles/secretmanager.secretAccessor` for
referenced secrets.

### In each customer Azure subscription

1. An app registration with a federated credential (issuer, subject and audience
   `api://AzureADTokenExchange` as shown in the UI). No client secret.
2. A resource group with a Container Apps environment (logs sent to Log Analytics) in the connection's
   region. Grant the app registration `Reader` and `Container Apps Contributor` on the group, and
   `Log Analytics Reader` on the workspace.
3. Optional: an Azure Container Registry, with `AcrPull` and `Container Registry Tasks Contributor` for the
   app registration (needed for repository sources and private images). Microsoft does not allow ACR
   Tasks on some subscription types (for example free-credit and student subscriptions); there, builds
   fail with Azure's "ACR Tasks requests … are not permitted" message and image sources still work.
4. Optional: a user-assigned managed identity named `cloudbridge-runtime` in the group, with `AcrPull` on the
   registry and `Key Vault Secrets User` on referenced vaults; grant the app registration
   `Managed Identity Operator` on it. Needed for private registry images and Key Vault secret references.
5. Metrics (Azure Monitor) are covered by `Reader` on the group; managed certificates for custom domains are
   created in the Container Apps environment (covered by `Container Apps Contributor`).

### Kubernetes clusters

- **AKS** (`auth: aks`): an AKS cluster with Microsoft Entra ID and Azure RBAC. Reuse the Azure app
  registration (same federated credential) and grant it **Azure Kubernetes Service RBAC Writer** on the
  namespace scope (`<cluster id>/namespaces/<namespace>`). The namespace must exist. The API server URL
  and its CA certificate come from `az aks show` / the cluster's kubeconfig.
- **Other clusters** (`auth: oidc`): configure the API server to accept tokens from CloudBridge's issuer
  with the connection's audience, and bind the resulting user to a namespace `Role` that can manage
  Deployments (including `deployments/scale`), Services and HorizontalPodAutoscalers, and read Pods and
  their logs. Secret references (`k8s:SECRET/KEY`) become `secretKeyRef`s; CloudBridge never reads them.
- Metrics use `metrics.k8s.io` when the cluster runs metrics-server (optional). Public services get a
  `LoadBalancer` Service; workers get no Service.

## How a deployment runs

`(awaiting_approval) → queued → validating → (building) → publishing → deploying → verifying → succeeded`

Deployments start from the UI, the API/CLI, a GitHub push, a pull request (preview applications) or a
promotion. With an approval gate they wait in `awaiting_approval` until an owner or admin approves.

1. **Validation**: connection is connected, settings fit the provider, repository host is public,
   provider access works (and, for builds, the registry repository and build project exist).
2. **Build** (repository sources): CodeBuild / Cloud Build / ACR Tasks clone the repository at the branch or
   commit and push an image. User input reaches the build only as quoted environment variables.
3. **Publishing**: the image is pinned to its immutable `sha256` digest.
4. **Provisioning**: App Runner `CreateService`/`UpdateService`, Cloud Run `services.create`/
   `patch` (plus the `allUsers` invoker binding for public services), or a Container Apps `PUT`
   (the new revision must be provisioned and running with the new image).
5. **Verification**: the provider reports the service running and, for public services, the health
   check path answers 2xx/3xx (only provider-issued hosts are contacted).

If verification fails and an earlier version was serving, it is restored automatically and the
deployment ends `rolled_back`. Rollbacks redeploy an earlier version's digest and configuration.
Cancellation is offered until provisioning starts.

## Project layout

```
app/
  main.py            App factory, middleware (request IDs, CSRF, body limit, security headers), error envelope
  config.py          Settings
  models.py, db.py   SQLAlchemy models (UTC timestamps on SQLite and PostgreSQL)
  schemas.py         Request/response models (match the frontend types)
  routers/           auth, applications, deployments, providers, audit, settings, platform (automation,
                     previews, promotion, schedules, metrics, costs, domains), GitHub hooks, alerts, API tokens
  services/          audit log, idempotency keys, rate limiting, spec validation, starting deployments,
                     previews, cost estimates, schedules, cloudbridge.yaml, alert URL checks
  providers/         adapter interface, AWS App Runner, ECS Express, Google Cloud Run, Azure Container Apps,
                     Kubernetes, Entra federation, capabilities, image digests
  worker/            deployment state machine, connection validation, deletion, health checks, schedules,
                     domain refresh, deferred pushes, e-mail and alert notifications
  cli.py             migrate / create-user / set-password
migrations/          Alembic
tests/               API, worker and adapter tests (botocore Stubber, recorded HTTP); no network
```
