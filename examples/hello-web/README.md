# hello-web

A tiny web server for testing CloudBridge deployments (Python standard library, no dependencies).

| Path | Response |
| ---- | -------- |
| `/` | HTML page with the version, instance and start time |
| `/healthz` | `{"status": "ok"}` (the health check CloudBridge uses) |
| `/api/info` | The same details as JSON |

Run locally: `python server.py`, then open http://localhost:8080 (`PORT` changes the port).

## Deploy with CloudBridge

The quickest way: **Applications → Create application → Use the sample app**. It fills in everything below;
you only choose the provider connection and region. Every CloudBridge user can do this to try a provider
end to end (build in your cloud, deploy, health check, logs, delete).

Or create an application by hand with:

- **Source:** Git repository, the repository URL, branch `main`, **Dockerfile path**
  `examples/hello-web/Dockerfile`, **build context** `examples/hello-web`
- **Runtime:** Python, port `8080`, health check `/healthz`, 0.25 vCPU / 512Mi (smallest, cheapest)

CloudBridge builds the image in your cloud (CodeBuild on AWS) and deploys it. `cloudbridge.yaml` holds
the same runtime settings for applications that read their configuration from the repository. The
repository must be public for CloudBridge to clone it.
