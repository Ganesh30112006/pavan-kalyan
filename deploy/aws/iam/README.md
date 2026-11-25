# IAM for CloudBridge's own AWS deployment

The policies of the roles in docs/deployment.md ("AWS with CI/CD"), as applied in account 492462083767:

| Role | Trust | Policies |
| ---- | ----- | -------- |
| `cloudbridge-github-deploy` | `trust-github.json` (GitHub OIDC, this repository's `main` only, immutable IDs) | `github-deploy-policy.json` (inline `DeployCloudBridge`) |
| `cloudbridge-platform` (task role) | `trust-tasks.json` | `platform-task-policy.json` (inline `CloudBridgePlatform`) |
| `cloudbridge-platform-execution` | `trust-tasks.json` | AWS managed `AmazonECSTaskExecutionRolePolicy` + `execution-policy.json` (inline `ReadCloudBridgeSecrets`) |
| `cloudbridge-platform-infrastructure` | `trust-infra.json` | AWS managed `AmazonECSInfrastructureRoleforExpressGatewayServices` |

ECS Express Mode registers task definitions and creates the underlying service as the caller, so the deploy
role needs those ECS actions as well (same list as the customer-side ECS policy in
`frontend/public/cloudbridge-connect.py`, without the delete actions).
