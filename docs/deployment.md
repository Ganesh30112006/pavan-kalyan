# Deploying CloudBridge

This guide is for the people who run CloudBridge for an organization. It covers the containers, a
single-host setup with Docker Compose, what to change for a larger setup, and day-two operations
(upgrades, probes, logs, backups). How customers connect their clouds is in `backend/README.md`.

## What runs

| Part | Image | Command | Notes |
| ---- | ----- | ------- | ----- |
| Web | `frontend/Dockerfile` | nginx (default) | Serves the web app and proxies `/api/` to the API on the same origin. Plain HTTP on port 8080 as an unprivileged user. |
| API | `backend/Dockerfile` | uvicorn (default) | Stateless apart from the database. Port 8000, reached only by the web container. |
| Worker | `backend/Dockerfile` | `python -m app.worker` | Runs deployments, validation, deletions, health checks and schedules. |
| Migrations | `backend/Dockerfile` | `python -m app.cli migrate` | One-off, once per release, before new API and worker processes start. |
| Database | PostgreSQL 16 | | SQLite is refused when `CLOUDBRIDGE_ENVIRONMENT=production`. |

TLS ends in front of the web container, at a load balancer, ingress or reverse proxy. If you'd rather
run nginx on a server that terminates TLS itself, `deploy/nginx.conf` is the equivalent configuration.

## Single host with Docker Compose

`deploy/compose.yaml` runs all five parts. Requirements: Docker with Compose v2 and about 1 GB of free memory.

```bash
cp deploy/cloudbridge.env.example deploy/cloudbridge.env   # then edit it (it stays out of git)
export POSTGRES_PASSWORD=$(openssl rand -hex 24)           # keep it in your secret store
docker compose -f deploy/compose.yaml up -d --build --wait
```

- Use a hex (or otherwise URL-safe) database password; it goes into a connection URL unescaped.
- The web app is then on `http://<host>:8080` (`CLOUDBRIDGE_HTTP_PORT` changes the port). Point your
  TLS-terminating proxy at it and open CloudBridge only through `https://`. Session cookies are `Secure`, so
  sign-in over plain HTTP works only on `localhost`.
- Create the first owner account:

  ```bash
  docker compose -f deploy/compose.yaml exec api python -m app.cli create-user \
    --email you@example.com --name "Your Name" --role owner --new-organization "Your Org"
  ```

  The password is prompted for. Add more users the same way, with `--organization-id <id>` (printed when
  the organization is created) instead of `--new-organization`. The web app can't invite users yet.

### Behind a load balancer

The web container passes the client's address to the API, which uses it for the sign-in rate limit. Set
`CLOUDBRIDGE_TRUSTED_PROXY` (environment of the `web` service, or in the shell before `docker compose up`)
to the address range of your load balancer, for example `10.0.0.0/16`. nginx then takes the client address
from the load balancer's `X-Forwarded-For` header. With the default (`127.0.0.1/32`), it trusts no proxy and
everyone behind the load balancer shares one rate limit.

## CloudBridge's own cloud identity

CloudBridge never stores customer keys. It signs in to customer accounts as itself, so the API and worker
need CloudBridge's own identity (see "CloudBridge's own identity" in `backend/README.md`):

- **AWS**: give the host or task an IAM role, and set `CLOUDBRIDGE_AWS_PRINCIPAL_ARN` to its ARN. The AWS SDK
  finds the role by itself. On EC2, set the instance metadata hop limit to 2
  (`aws ec2 modify-instance-metadata-options --http-put-response-hop-limit 2 ...`) so containers can reach it.
  Don't put access keys in `cloudbridge.env`.
- **Azure and AKS**: AWS outbound identity federation (`CLOUDBRIDGE_AZURE_TOKEN_SOURCE=aws`), set up once.

## Configuration checks in production

With `CLOUDBRIDGE_ENVIRONMENT=production` (the compose file sets it), the API and worker refuse to start when:

- `CLOUDBRIDGE_DATABASE_URL` points to SQLite,
- `CLOUDBRIDGE_COOKIE_SECURE` is `false`,
- `CLOUDBRIDGE_PUBLIC_URL` is set but doesn't start with `https://`,
- `CLOUDBRIDGE_CORS_ORIGINS` contains `*`, or `CLOUDBRIDGE_ALLOW_PRIVATE_ALERT_URLS` is on.

The API docs (`/api/v1/docs`) are off in production.

## Probes

| Path | Answers | Use it for |
| ---- | ------- | ---------- |
| `GET /api/v1/health` | `200 {"status":"ok"}` whenever the API process answers. It doesn't touch the database. | Liveness: restart the API when this fails. |
| `GET /api/v1/ready` | `200` when ready, otherwise `503` with the failing checks. | Readiness: send traffic only while this answers 200. |
| `GET /healthz` (web container) | `200 ok` | Liveness of nginx. |

`/api/v1/ready` reports these checks:

- `database`: `ok` or `unreachable`. Connections time out after 10 seconds.
- `schema`: one of
  - `ok`: the database matches this release.
  - `not_migrated` or `behind`: migrate hasn't run for this release yet.
  - `newer`: a newer release has already migrated the database. This still passes, so old processes keep
    serving during a rolling upgrade.
- `worker` (only when the worker runs inside the API process): `ok` or `stopped`.

Both probes answer during maintenance mode, so a maintenance window doesn't take every instance out of the
load balancer. They aren't written to the request log.

The worker has no HTTP port. If its loop ever stops, the process exits with status 1, and your supervisor
(Compose `restart: unless-stopped`, Kubernetes, systemd) restarts it.

## Scaling

- **API**: run as many replicas as you like behind the web tier or load balancer. The sign-in rate limit is
  kept in each process's memory, so with N replicas a client gets up to N times
  `CLOUDBRIDGE_LOGIN_MAX_ATTEMPTS` attempts per window.
- **Worker**: one is enough for most installations. Several are safe: each step is claimed with a database
  lease, so two workers never run the same step. A worker that stops in the middle of a deployment has its
  step resumed by another one when the lease expires (10 minutes).
- **Small installations** can run the worker inside the API process (`CLOUDBRIDGE_RUN_WORKER=true`, the
  default outside the compose file). In that case run only one API process.

## Upgrades

1. Build (or pull) the new images.
2. Run migrations with the new image: `docker compose -f deploy/compose.yaml run --rm migrate`. The running
   release keeps working, and its readiness reports `schema: newer`. This relies on a rule for anyone writing
   migrations: a release may only add tables, columns and indexes. Removing or renaming something waits for
   the next release, once no running code uses it.
3. Replace the API and worker processes (`docker compose -f deploy/compose.yaml up -d --wait`). A worker
   that is stopped mid-deployment loses nothing: the next worker resumes the step from its saved state.

On shutdown the API gives open requests 20 seconds to finish. The compose file allows 30 seconds for the API
and 40 for the worker before it kills the process.

To roll back to an older release, deploy the older images without running its migrations. Its readiness
reports `schema: newer` and it keeps serving.

## Logs

The compose file sets `CLOUDBRIDGE_LOG_FORMAT=json`: one JSON object per line on stdout. Each line has
`time`, `level`, `logger` and `message`, plus `exception` for errors. Every API request is logged once by
the `cloudbridge.access` logger with `request_id`, `method`, `path` (never the query string), `status` and
`duration_ms`. The same `request_id` is returned in the `X-Request-ID` header and in every error response,
so a user's error message leads straight to its log line. Secret values are never logged.

While the database is unreachable, the worker logs one warning when the outage starts and one line when the
database is back, rather than an error every few seconds.

## Request size limits

API request bodies are limited to 1 MB (`CLOUDBRIDGE_MAX_BODY_BYTES`). GitHub webhook deliveries
(`/api/v1/hooks/`) are limited to 25 MB (`CLOUDBRIDGE_MAX_WEBHOOK_BODY_BYTES`), the most GitHub sends. The
nginx configurations apply the same limits before a request reaches the API. A request over the limit gets
`413` with the standard error body.

## Backups

Everything CloudBridge knows is in PostgreSQL: users, connections, applications, deployment history and the
audit log. Customer secrets are never stored there, only references to them. Back the database up the way
you back up any PostgreSQL database. On a single host:

```bash
docker compose -f deploy/compose.yaml exec -T db pg_dump -U cloudbridge -Fc cloudbridge > cloudbridge-$(date +%F).dump
```

For larger installations, use a managed PostgreSQL with point-in-time recovery and set
`CLOUDBRIDGE_DATABASE_URL` to it (then drop the `db` service).

## Images and patching

- Both images run as non-root users.
- The web image is built on mainline nginx with Alpine packages upgraded at build time. The API image drops
  the build-only Python packages.
- Rebuild both images regularly (for example weekly, and whenever a base-image CVE is published), and pin
  the resulting digests for each release in your registry.
- Source maps are removed from the web image, and the build fails if demo code reaches the bundle.
