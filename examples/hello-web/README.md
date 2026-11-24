# hello-web

A tiny web server for testing CloudBridge deployments (Python standard library, no dependencies).

| Path | Response |
| ---- | -------- |
| `/` | HTML page with the version, instance and start time |
| `/healthz` | `{"status": "ok"}` (the health check CloudBridge uses) |
| `/api/info` | The same details as JSON |

Run locally: `python server.py`, then open http://localhost:8080 (`PORT` changes the port).

## Deploy with CloudBridge

Create an application with:

- **Source:** Git repository, the repository URL, branch `main`, **Dockerfile path**
  `examples/hello-web/Dockerfile`, **build context** `examples/hello-web`
- **Runtime:** Python, port `8080`, health check `/healthz`, 0.25 vCPU / 512Mi (smallest, cheapest)

CloudBridge builds the image in your cloud (CodeBuild on AWS) and deploys it. `cloudbridge.yaml` holds
the same runtime settings for applications that read their configuration from the repository. The
repository must be public for CloudBridge to clone it.
