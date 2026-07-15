# Generating a typed API client (G38)

The API serves its full OpenAPI 3.1 schema at `GET /openapi.json` (public, unauthenticated).
A typed client is generated from that schema with an off-the-shelf generator — we deliberately
**do not** vendor the generator as a project dependency, so client generation is a reproducible,
opt-in step rather than part of the backend runtime footprint.

## 1. Export the schema

With the API running (or via the app object), capture the live schema:

```bash
# From a running instance:
curl -s http://localhost:8000/openapi.json > openapi.json

# …or offline, straight from the app (no server needed):
python -c "import json; from src.main import app; print(json.dumps(app.openapi()))" > openapi.json
```

## 2a. Python client (`openapi-python-client`)

```bash
# One-off, no project dependency added:
pipx run openapi-python-client generate --path openapi.json --meta pyproject
# -> ./deallens-diligence-lab-api-client/  (a typed, pip-installable package)
```

## 2b. TypeScript types (`openapi-typescript`)

```bash
npx --yes openapi-typescript openapi.json -o deallens-api.d.ts
# -> deallens-api.d.ts  (fully typed request/response shapes for a fetch wrapper)
```

## Authenticating the generated client

Programmatic callers authenticate with a **scoped API key** (`dlk_…`), minted by an org admin via
`POST /api/organizations/{organization_id}/api-keys`. Send it as a bearer token:

```
Authorization: Bearer dlk_<secret>
```

The key is bound to its organization (the tenant guard still applies) and may only exercise the
scopes it was granted (e.g. `read:underwriting`, `write:underwriting`). The plaintext secret is
shown exactly once at creation and stored only as a SHA-256 digest — treat it like a password.
