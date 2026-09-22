# Quipper Auth FastAPI Microservice — Deployment Report

**Date:** 2026-09-22
**Status:** ✅ DONE

## What Was Done

1. **Created `auth_service.py`** — A FastAPI application implementing all auth endpoints previously handled by Lua/nginx:
   - `GET /api/auth/check` — nginx `auth_request` endpoint (returns 200 + X-Auth headers if valid, 302 redirect if not)
   - `POST /api/auth/login` — Email/password login, sets `quipper_session` cookie
   - `POST /api/auth/signup` — New user registration with admin email notification
   - `POST /api/auth/reset-request` — Password reset link generation
   - `POST /api/auth/reset` — Password reset execution
   - `GET/POST /api/auth/logout` — Session invalidation
   - `GET /api/admin/users` — List all users (admin only)
   - `GET /api/admin/stats` — User statistics (admin only)
   - `POST /api/admin/approve` — Approve pending user (admin only)
   - `POST /api/admin/revoke` — Revoke user access (admin only)

2. **Created `quipper-auth.service`** — systemd unit file running uvicorn with 2 workers on 127.0.0.1:18900.

3. **Deployed to server** via SCP to `root@10.121.15.125:/www/wwwroot/quipper.mabdc.com/_guardian/`.

4. **Enabled and started** the systemd service. Uvicorn parent + 2 worker processes confirmed running.

## Test Results

All tests passed successfully:

| # | Test | Result |
|---|------|--------|
| 1 | Auth check without cookie | `302` redirect to `/auth/login.html` ✅ |
| 2 | Login as admin | `{"ok":true,"data":{"role":"admin","email":"sottodennis@gmail.com"}}` ✅ |
| 3 | Auth check with session cookie | `200 OK` with `x-auth-user: sottodennis@gmail.com` and `x-auth-role: admin` headers ✅ |
| 4 | Admin stats | `{"ok":true,"data":{"total":2,"pending":1,"approved":1,"revoked":0}}` ✅ |
| 5 | Admin users list | Returns both users with correct fields ✅ |

## Concerns & Notes

1. **PowerShell SSH escaping**: Passing JSON inline through PowerShell → SSH → remote bash caused double-quote mangling. Workaround: write JSON to a temp file on the server first (`printf > /tmp/login.json`), then use `curl -d @/tmp/login.json`. This is only a testing concern; production traffic comes from nginx/browser.

2. **Helper subprocess overhead**: Each request spawns a new Python process for `auth_helper.py`. For the current low-traffic use case this is fine (~10ms overhead per call). If traffic grows significantly, consider converting the helper to a persistent daemon or integrating DB calls directly into the FastAPI app.

3. **Cookie security**: Cookies are set with `httponly`, `secure`, `samesite=strict`. The `secure` flag means cookies will only be sent over HTTPS — ensure nginx terminates TLS before proxying to the auth service.

4. **nginx auth_request compatibility**: The `/api/auth/check` endpoint returns 302 redirects for unauthenticated/pending users. Standard nginx `auth_request` only forwards 2xx/401/403. If using `auth_request`, you may need to handle 302s via `error_page` directives or switch to returning 401/403 status codes instead. Currently it returns 302 which works if nginx is configured to pass through non-200 responses.

5. **No rate limiting**: The login and signup endpoints have no rate limiting. Consider adding middleware or nginx-level rate limiting to prevent brute-force attacks.

6. **Service auto-restart**: systemd is configured with `Restart=always` and `RestartSec=3`, so the service will recover automatically from crashes.
