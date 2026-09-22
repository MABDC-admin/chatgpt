# Quipper Auth System — Design Spec

**Date:** 2026-09-22
**Target:** quipper.mabdc.com
**Architecture:** Nginx Lua + SQLite + Python helpers + curl SMTP

## Overview

Gate the entire Quipper SPA behind email/password authentication with admin approval workflow. All auth logic runs inside the existing nginx process via `content_by_lua_file` and `access_by_lua_file`. No new containers or services.

## Components

### 1. SQLite Database (`/www/wwwroot/quipper.mabdc.com/_guardian/auth.db`)

```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    role TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now')),
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
```

Auto-seeded on first run with admin: `sottodennis@gmail.com` / bcrypt(`Denskie123`).

### 2. Python Helper (`/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py`)

A single Python 3 script handling operations that are hard in pure Lua:
- **bcrypt hashing/verification** (via `python3-bcrypt`)
- **SQLite read/write** (via built-in `sqlite3`)
- **Token generation** (via `secrets.token_hex(32)`)

Called by Lua scripts via `io.popen()` with JSON stdin/stdout protocol.

Commands: `hash_password`, `verify_password`, `create_user`, `get_user_by_email`, `list_users`, `approve_user`, `revoke_user`, `create_token`, `verify_token`, `delete_tokens`, `seed_admin`, `get_stats`

### 3. SMTP Email (`/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh`)

Shell script using `curl --ssl-reqd` for STARTTLS:
- Host: `mail.mabdc.ae:587`
- User: `admin@mabdc.ae`
- Pass: `Denskie123`
- From: `Quipper Admin <admin@mabdc.ae>`

Emails sent:
| Trigger | To | Subject |
|---------|----|--------|
| New signup | `sottodennis@gmail.com` | "New Quipper access request from [email]" |
| Approved | User email | "Your Quipper account has been approved" |
| Revoked | User email | "Your Quipper access has been revoked" |
| Password reset | User email | "Reset your Quipper password" |

### 4. Lua Scripts (`/www/wwwroot/quipper.mabdc.com/_guardian/lua/`)

| Script | Purpose |
|--------|--------|
| `access_gate.lua` | `access_by_lua_file` on `/` — checks session cookie, redirects to `/auth/login.html` if missing/invalid |
| `api_router.lua` | Routes `/api/auth/*` and `/api/admin/*` to correct handler |
| `lib.lua` | Shared utilities: JSON encode/decode, popen helper, cookie parsing |
| `api_login.lua` | POST `/api/auth/login` |
| `api_signup.lua` | POST `/api/auth/signup` |
| `api_reset_request.lua` | POST `/api/auth/reset-request` |
| `api_reset.lua` | POST `/api/auth/reset` |
| `api_logout.lua` | POST `/api/auth/logout` |
| `api_admin_users.lua` | GET `/api/admin/users` |
| `api_admin_approve.lua` | POST `/api/admin/approve` |
| `api_admin_revoke.lua` | POST `/api/admin/revoke` |
| `api_admin_stats.lua` | GET `/api/admin/stats` |

### 5. Static HTML Pages (`/www/wwwroot/quipper.mabdc.com/auth/`)

| File | Purpose |
|------|--------|
| `login.html` | Login form |
| `signup.html` | Signup form |
| `reset-request.html` | "Forgot password" form |
| `reset.html` | New password form (token in URL) |
| `admin.html` | Admin dashboard (approve/revoke UI) |
| `pending.html` | "Awaiting approval" message shown after signup |

All pages use Tailwind CSS via CDN for styling. Vanilla JS calls API endpoints.

### 6. Session Cookie

- Name: `quipper_session`
- Value: random 64-char hex token
- Attributes: `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=604800` (7 days)
- Stored hashed (SHA-256) in `tokens` table

## Security

- Passwords: bcrypt via `python3-bcrypt`
- Tokens: SHA-256 hash stored in DB, raw value only in cookie/URL
- Rate limiting: nginx `limit_req_zone` on `/api/auth/*` (10 req/min per IP)
- CSRF: double-submit cookie pattern for POST requests
- Reset tokens: expire in 1 hour
- No sensitive data in client-side storage

## Nginx Vhost Changes

Add to `quipper.mabdc.com.conf`:
```
# Auth pages (public)
location /auth/ {
    try_files $uri =404;
}

# Auth API endpoints
location /api/auth/ {
    content_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/api_router.lua;
}

# Admin API endpoints
location /api/admin/ {
    content_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/api_router.lua;
}

# Gate everything else
location / {
    access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
    try_files $uri $uri/ /index.html;
}
```

## File Layout

```
/www/wwwroot/quipper.mabdc.com/_guardian/
├── auth.db
├── auth_helper.py
├── send_mail.sh
└── lua/
    ├── access_gate.lua
    ├── api_router.lua
    ├── lib.lua
    ├── api_login.lua
    ├── api_signup.lua
    ├── api_reset_request.lua
    ├── api_reset.lua
    ├── api_logout.lua
    ├── api_admin_users.lua
    ├── api_admin_approve.lua
    ├── api_admin_revoke.lua
    └── api_admin_stats.lua

/www/wwwroot/quipper.mabdc.com/auth/
├── login.html
├── signup.html
├── reset-request.html
├── reset.html
├── admin.html
└── pending.html
```
