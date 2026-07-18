# Deploying the public demo on a single VPS

This is the copy-paste path to a live, HTTPS demo at `https://demo.yourdomain.com`, running the
whole stack (Postgres + pgvector, API, webhook worker, build worker, demo-cleanup worker, web app)
behind Caddy on one small box. It complements [deploy-demo.md](deploy-demo.md), which explains the
`DEMO_MODE` posture in detail.

Everything below is a configuration exercise — the app ships production-shaped Docker images and a
hardened demo mode. Budget ~30–45 minutes.

## 0. What you need

- **A VPS** running Ubuntu 24.04. Recommended: **2 vCPU / 4 GB RAM / ~40 GB disk** — the Next.js
  production build is memory-hungry, so 4 GB avoids an OOM during `up --build`. Good value:
  Hetzner CX22 (~€4.5/mo), DigitalOcean 4 GB (~$24) or a Vultr/Linode 4 GB (~$24). On a 2 GB box,
  add swap first (see Troubleshooting) or build the images on a larger machine and push to a
  registry.
- **A domain** you control, with the ability to add a DNS record.
- **~10 minutes of SEC-polite patience** on first build of any ticker (subsequent visitors hit the
  EDGAR cache).

## 1. Point DNS at the box

Create an **A record** (and AAAA if you have IPv6) for `demo.yourdomain.com` → your VPS IP.
Do this first so Caddy can complete the TLS challenge on first boot. Verify:

```bash
dig +short demo.yourdomain.com    # should print your VPS IP
```

## 2. Prepare the box

SSH in as a sudo user, then:

```bash
# Firewall: allow SSH + HTTP + HTTPS only
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw --force enable

# Install Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker   # pick up the group without re-login
```

## 3. Get the code and configure

```bash
git clone https://github.com/ahines99/deallens-diligence-lab.git
cd deallens-diligence-lab

cp .env.prod.example .env
nano .env         # set PUBLIC_DOMAIN and SEC_USER_AGENT at minimum
```

Minimum edits in `.env`:

- `PUBLIC_DOMAIN=demo.yourdomain.com`
- `SEC_USER_AGENT=DealLens demo (Your Name) you@example.com`  ← use a real contact

Leave `LLM_MODE=mock` for a zero-cost demo. (See §8 to turn on the live LLM later.)

## 4. Launch

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

This builds the images, starts Postgres, applies migrations on API boot, starts the two workers +
demo-cleanup, starts the web app, and brings up Caddy — which fetches a TLS certificate for your
domain automatically. Watch it settle:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps      # all should be healthy
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f caddy   # look for "certificate obtained"
```

## 5. Bootstrap the owner account

Open `https://demo.yourdomain.com/register` and create the **first** account — this bootstraps the
installation as owner. Self-registration is then disabled (`AUTH_ALLOW_REGISTRATION=false`), so this
is a one-time step you do before sharing the link.

## 6. Verify end-to-end

- Visit `https://demo.yourdomain.com` → padlock is valid.
- Click **"Try the demo"** → you get a guest session in the Demo Sandbox org.
- Create a workspace from a ticker (e.g. `MSFT`) → the build timeline runs and a workspace appears.
- Try **"Load the private-deal example"** to see the governed diligence/IC workflow with no external
  calls.

That's a shareable portfolio demo. Add the URL to your README and pin the repo on your GitHub
profile.

## 7. Operations

```bash
# Alias to save typing
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'

dc ps                      # health
dc logs -f api             # follow API logs
dc restart web             # restart one service

# Update to the latest code
git pull && dc up --build -d

# Disk hygiene: the EDGAR cache is TTL-bounded but not size-bounded
du -sh apps/api/data/cache
find apps/api/data/cache -mtime +7 -delete   # optional weekly trim (or lower EDGAR_CACHE_TTL_SECONDS)

# Back up the database
dc exec db pg_dump -U deallens deallens | gzip > backup-$(date +%F).sql.gz
```

The `demo-cleanup` worker runs hourly on its own and purges only Demo Sandbox data older than
`DEMO_RETENTION_HOURS`. Non-demo organizations are never touched.

## 8. (Optional) Turn on the live LLM later

Showcase real grounded synthesis / LLM extraction with a hard spend ceiling:

```bash
# in .env
LLM_MODE=live
LLM_API_KEY=sk-ant-...            # your Anthropic key
ORG_LLM_QUOTA_PER_HOUR=20         # caps LLM-capable requests for the shared demo org per hour
```

Then `dc up -d`. Only workspaces whose owner enabled external-LLM consent (and whose classification
is not `restricted`) ever call out; when the quota trips, callers get a 429 that says the
deterministic endpoints remain available. See [deploy-demo.md §live-LLM](deploy-demo.md) for the
spend math before you announce the URL.

## Troubleshooting

- **`up --build` gets killed / OOM on a 2 GB box** — add swap, then retry:
  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```
- **Caddy can't get a certificate** — DNS isn't pointing at the box yet, or ports 80/443 are
  blocked. Confirm `dig +short demo.yourdomain.com` matches the IP and `ufw status` shows 80,443
  allowed. Caddy retries automatically.
- **502 from the site** — the web container isn't healthy yet (it waits on the API, which applies
  migrations on boot). `dc logs api` then `dc logs web`.
- **Validate the merged config without starting anything**:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.prod.yml config
  ```
