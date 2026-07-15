# Hosting a public demo

The repo ships a hardened demo posture so a public instance is a configuration decision,
not an engineering project. Everything below is optional and off by default.

## What DEMO_MODE adds

| Concern | Mechanism |
|---|---|
| Zero-friction access | `POST /api/auth/demo` mints a real guest account (member role) inside a shared **Demo Sandbox** organization; the login page shows a "Try the demo" button. Guests use the same auth, tenancy, and governance paths as any user — nothing is bypassed. |
| SEC fair access | SEC-bound build endpoints (`POST /api/workspaces`, `build/retry`, `refresh`, `sec/ingest`) are throttled per client IP (`DEMO_BUILDS_PER_HOUR`, default 6). Reads are never throttled. |
| Repeat-visitor speed | `EDGAR_CACHE_TTL_SECONDS` caches EDGAR JSON and filing documents on disk, so the second visitor to build "NVDA" gets a near-instant workspace. Recommended: `21600` (6 h). |
| Disk/data growth | `python -m src.workers.demo_cleanup` purges demo-sandbox workspaces/deals and guest identities older than `DEMO_RETENTION_HOURS` (default 72), plus expired sessions. Run it hourly (`--interval 3600`) or from cron with `--once`. It never scans non-demo organizations. |

## Minimal deployment (single VPS or Fly.io/Railway-class host)

1. Copy `.env.example` to `.env` and set at minimum:

   ```bash
   SEC_USER_AGENT="DealLens demo (your name) you@example.com"   # required by SEC
   DEMO_MODE=true
   EDGAR_CACHE_TTL_SECONDS=21600
   WEBHOOK_ENCRYPTION_KEY=<fernet key>        # if webhooks will be shown
   ```

2. `docker compose up --build -d` — brings up Postgres, the API (migrations apply on boot),
   the webhook worker, the build worker (durable job queue), and the web app bound to localhost.

3. Add the cleanup worker to the schedule. Either extend `docker-compose.yml` with a service:

   ```yaml
   demo-cleanup:
     build: ./apps/api
     command: python -m src.workers.demo_cleanup --interval 3600
     environment: *api-environment   # same env as the api service
     depends_on:
       db:
         condition: service_healthy
   ```

   or run `docker compose exec api python -m src.workers.demo_cleanup --once` from host cron.

4. Put a TLS reverse proxy (Caddy is the least-effort: two-line Caddyfile) in front of the
   web app's port 3000. The compose file deliberately binds services to `127.0.0.1` — only
   the proxy should be exposed. The API port does not need to be public at all: the web
   app's same-origin `/backend` proxy reaches it over the compose network.

5. Register the first owner account at `/register` yourself before announcing the URL
   (the first registration bootstraps the installation; later self-signups stay disabled).
   Guests then use "Try the demo" — they never see the registration flow.

## Operating notes

- Guests cannot log back in (their credential is random and undisclosed); a demo session
  lasts `AUTH_SESSION_HOURS` and their data lasts `DEMO_RETENTION_HOURS`.
- The per-IP limiter is in-process. The single-process compose deployment makes it
  immediately effective; if you scale API replicas, add an edge rate limit too.
- Watch the box's disk: the EDGAR cache is bounded by TTL but not by size. A weekly
  `find apps/api/data/cache -mtime +7 -delete` (or a smaller TTL) is plenty.
- All demo endpoints respect `AUTH_REQUIRED=true`. Never run a public instance with
  `AUTH_REQUIRED=false`.
