# CloudBridge web app: the static build served by nginx, which also proxies /api/ to the API container on the
# same origin (cookies stay first-party, no CORS). Build: docker build -t cloudbridge-web frontend/
# Listens on plain HTTP 8080 as an unprivileged user; put a TLS-terminating load balancer or ingress in front.

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Same-origin API, never the demo backend. Only public values belong in VITE_* (they end up in the bundle).
ARG VITE_SUPPORT_URL=""
ENV VITE_API_BASE_URL="" VITE_DEMO_MODE="false" VITE_SUPPORT_URL=${VITE_SUPPORT_URL}
RUN npm run build \
    && if grep -l "createDemoApi" dist/assets/*.js; then echo "demo code in the bundle" >&2; exit 1; fi \
    && find dist -name '*.map' -delete \
    && rm -rf dist/__pycache__

# Mainline nginx is the branch nginx recommends and gets security fixes first; Alpine packages are upgraded on
# top of the base image. Rebuild regularly, and pin the resulting digest in your registry for each release.
FROM nginxinc/nginx-unprivileged:mainline-alpine
USER root
RUN apk upgrade --no-cache
USER 101
# Rendered to /etc/nginx/conf.d/default.conf at start (envsubst replaces only the variables set below).
ENV CLOUDBRIDGE_API_UPSTREAM="http://api:8000" \
    CLOUDBRIDGE_TRUSTED_PROXY="127.0.0.1/32" \
    CLOUDBRIDGE_DNS_RESOLVER="127.0.0.11" \
    NGINX_ENVSUBST_FILTER="^CLOUDBRIDGE_"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
