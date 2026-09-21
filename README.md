# Teacher AI Cloud Platform

A private, ChatGPT-style AI platform for schools: streaming chat, image generation,
role-based access control and per-user AI credit budgets.

- **Frontend** — Next.js 16 (App Router), streaming SSE chat UI
- **Backend** — FastAPI, async SQLAlchemy 2.0
- **Data** — PostgreSQL 17 + pgvector, Redis
- **Edge** — Nginx (SSE buffering disabled), Cloudflare Tunnel

## Layout

```
backend/app/
  main.py          app wiring, DB init, bootstrap admin seed
  config.py        env-driven settings
  rbac.py          8 roles -> permission sets (single source of truth)
  models.py        users, credits, conversations, messages, usage, audit, documents
  deps.py          current_user, require(Permission), audit helper
  routers/
    auth.py        login, /me, /me/credits
    chat.py        SSE streaming chat + conversation CRUD
    images.py      gpt-image-1 generation + gallery
    admin.py       users, roles, credit allocation, usage reports, audit trail
  services/
    ai.py          OpenAI client + cost-optimisation model router
    credits.py     allocation, monthly rollover, ledger writes
    pricing.py     provider price table (verify before billing anyone)
frontend/
  app/login  app/chat  app/images  app/admin
  lib/api.ts       fetch wrapper + token handling
  lib/stream.ts    SSE frame parser (EventSource can't POST or send auth headers)
```

## Roles

`SUPER_ADMIN` `ADMIN` `PRINCIPAL` `REGISTRAR` `TEACHER` `STUDENT` `PARENT` `IT_ADMIN`

Permissions derive from the role in `backend/app/rbac.py` — they ship with a deploy
and are reviewable in git rather than editable at runtime. Only a `SUPER_ADMIN` may
grant an administrator role.

## Credits

Every billable call writes exactly one `usage_logs` row and deducts from the user's
`credit_accounts.used_cents`. Amounts are **cents of real provider spend**, so the
admin dashboard shows true cost, not an invented currency. An allocation of `0`
means unmetered. Usage resets on the first of each calendar month.

Prices live in `backend/app/services/pricing.py`. They are list prices and they
change — check them against current provider pricing before charging anyone.

## Configure

```bash
cp .env.example .env
openssl rand -hex 32          # -> JWT_SECRET
```

Required: `POSTGRES_PASSWORD`, `JWT_SECRET`, `OPENAI_API_KEY`, `SEED_ADMIN_PASSWORD`.
Compose refuses to start if any are missing, rather than booting insecurely.

## Run

```bash
docker compose up -d --build
docker compose logs -f backend
```

On an empty database the backend creates the schema, enables `pgvector`, and seeds
one `SUPER_ADMIN` from `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`. **Sign in and
change that password immediately** — it is in your `.env` in plain text.

API docs: `http://<host>:<HTTP_PORT>/api/docs`

## Deploying behind Cloudflare Tunnel

The server has no public IP, so the hostname must route through `cloudflared`.
Bind the stack to a free local port and add an ingress rule:

```yaml
# /etc/cloudflared/<tunnel>.yml
ingress:
  - hostname: chat.mabdc.com
    service: http://127.0.0.1:8300
  - service: http_status:404
```

Set `HTTP_PORT=8300` and `PUBLIC_URL=https://chat.mabdc.com` in `.env`, then
restart the tunnel. Cloudflare terminates TLS, so the stack's Nginx serves plain
HTTP on the loopback port.

## Phase status

| Phase | State |
|---|---|
| 1 Foundation — Docker, Postgres, Redis, auth, RBAC | done |
| 2 ChatGPT core — streaming, history, model routing | done |
| 3 Teacher tools — lesson planner, assessments, worksheets | not started |
| 4 Image generation — prompt, gallery, credit deduction | done |
| 5 School knowledge AI — upload, extract, embed, retrieve | schema only |
| 6 Admin platform — users, credits, usage, audit | done |
| 7 Deployment — compose, Nginx, tunnel | config ready |

Phase 5 has its tables (`documents`, `document_chunks` with an HNSW cosine index)
but no upload, extraction or retrieval endpoints yet. `use_knowledge_base` on the
chat request is accepted and currently ignored.

## Before real classroom use

- Replace `Base.metadata.create_all` with Alembic migrations — the current path
  cannot alter an existing table.
- Add rate limiting (Redis is running and unused).
- Set up `pg_dump` backups of the platform database.
