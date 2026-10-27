# CloudBridge CLI and GitHub Action

`cloudbridge.py` is a single-file client that needs only Python 3.10+.

1. In CloudBridge, open **Settings → API tokens** and create a token. It is shown once; it acts as you,
   with your role.
2. Point the CLI at CloudBridge:

   ```bash
   export CLOUDBRIDGE_URL=https://cloudbridge.example.com
   export CLOUDBRIDGE_TOKEN=cbt_...
   # or: python cloudbridge.py login --url https://cloudbridge.example.com --token cbt_...
   ```

| Command | What it does |
| ------- | ------------ |
| `apps` | List applications with provider, status, health and version |
| `status APP` | Show one application |
| `deploy APP [--wait]` | Deploy the saved configuration; `--wait` follows it and exits 1 if it fails |
| `promote APP [--wait]` | Deploy APP's running image to its promotion target (e.g. staging → production) |
| `approve DEPLOYMENT [--reject]` | Approve or reject a deployment waiting for approval |
| `apply APP [cloudbridge.yaml]` | Make a `cloudbridge.yaml` the application's requested configuration |
| `logs APP` | Recent runtime logs |
| `metrics APP [--range 1h]` | Requests, errors, latency, instances, CPU and memory |

## GitHub Action

```yaml
- uses: YOUR-ORG/cloudbridge/cli@main
  with:
    url: https://cloudbridge.example.com
    token: ${{ secrets.CLOUDBRIDGE_TOKEN }}
    application: orders-api
    command: deploy        # or promote, apply
    wait: "true"
```

To deploy on every push without a workflow, turn on **Auto-deploy** in the application's
**Automation** settings and add the webhook CloudBridge shows to the GitHub repository instead.

## Connecting a cloud account

`frontend/public/cloudbridge-connect.py` (served by the app at `/cloudbridge-connect.py`) sets up a
customer's AWS account, Azure subscription or Kubernetes cluster for CloudBridge. Don't assemble its
arguments by hand: the connection page shows the full command. Run it in AWS CloudShell or Azure Cloud
Shell; it asks before every change, and `--dry-run` only lists the steps.
