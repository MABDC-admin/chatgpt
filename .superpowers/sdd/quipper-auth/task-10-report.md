# Task 10 Report: Nginx Vhost Configuration for Quipper Auth System

**Date:** 2026-09-22
**Status:** DONE (with one expected integration dependency)

## What Was Done

### 1. Nginx Configuration Written and Deployed

Created the nginx vhost config at `C:\Users\DENNIS\Downloads\CHATGPT-CLONE\_guardian\quipper-nginx.conf` and uploaded it to the server at `/www/server/panel/vhost/nginx/quipper.mabdc.com.conf`.

**Architecture:** Uses nginx `auth_request` module pointing to the FastAPI auth service on `127.0.0.1:18900` instead of Lua.

**Key design decisions:**
- HTTP → HTTPS redirect on port 80
- SSL with TLSv1.2/1.3 and strong cipher suite
- Security headers (X-Frame-Options, HSTS, etc.)
- Internal auth subrequest at `/.internal/auth-check` (not externally accessible)
- Named location blocks for auth error handling (`@unauthenticated`, `@unauthorized`, `@pending_approval`)

### 2. Route Classification

| Location | Auth Required | Notes |
|----------|--------------|-------|
| `/auth/` | No | Login, signup, pending, reset HTML pages |
| `/api/auth/` | No | Auth API endpoints (proxied to FastAPI :18900) |
| `/edu-files/` | No | MinIO proxy for Teacher Tool files |
| `/api/curriculum/` | No | MinIO proxy for curriculum JSON data |
| `/assets/` | No | Static assets (1 year cache, immutable) |
| `/api/admin/` | Yes | Admin API (proxied to FastAPI :18900) |
| `/` (everything else) | Yes | React SPA fallback |

### 3. Bug Fix During Deployment

Initial config had `limit_req_zone` inside the `server` block, which is not allowed — it must be in the `http` context. Removed the `limit_req_zone` directive and the corresponding `limit_req` reference since rate limiting can be handled by the FastAPI service directly or added to the main `nginx.conf` http block later if needed.

### 4. Nginx Test and Reload

```
nginx -t → syntax ok, test successful
nginx -s reload → success
```

(Note: pre-existing warning about duplicate MIME type in `library.sfxsai.com.conf` is unrelated.)

## Verification Results

Tests run from the server using `--resolve quipper.mabdc.com:443:127.0.0.1` to hit HTTPS directly:

| Endpoint | Expected | Actual | Status |
|----------|----------|--------|--------|
| `GET /` (no cookie) | 302 → /auth/login.html | 500 | ⚠️ See below |
| `GET /auth/login.html` | 200 | 200 | ✅ |
| `POST /api/auth/login` (bad creds) | JSON error | `{"ok":false,"error":"Invalid email or password"}` | ✅ |
| `GET /assets/` | 200 or 403 | 403 | ✅ (no index file, directory listing off — correct behavior) |

### Root Cause of 500 on Protected Routes

The nginx error log shows:
```
auth request unexpected status: 302 while sending to client
```

The FastAPI `/api/auth/check` endpoint is currently returning **HTTP 302** (redirect). However, nginx `auth_request` only understands:
- **2xx** → allow access
- **401** → deny (triggers `error_page 401`)
- **403** → deny (triggers `error_page 403`)

Any other status code (including 302) causes nginx to return 500 to the client.

## ⚠️ CRITICAL NOTE FOR FASTAPI AUTH SERVICE DEVELOPER

The `/api/auth/check` endpoint in the FastAPI service **MUST** be updated to return proper HTTP status codes for nginx `auth_request` compatibility:

| Condition | HTTP Status | Nginx Behavior |
|-----------|-------------|----------------|
| User is authenticated and approved | **200 OK** | Allow request through to upstream |
| User is not authenticated (no cookie / invalid session) | **401 Unauthorized** | Triggers `@unauthenticated` → 302 redirect to `/auth/login.html` |
| User is authenticated but NOT approved (pending) | **403 Forbidden** | Triggers `@pending_approval` → 302 redirect to `/auth/pending.html` |

**Do NOT return 302 redirects from `/api/auth/check`.** The redirect logic is handled entirely by nginx named locations (`@unauthenticated`, `@pending_approval`). The FastAPI endpoint should only return status codes and optionally set response headers like `X-Auth-User` and `X-Auth-Role` which can be captured via `auth_request_set`.

Example FastAPI implementation:
```python
@app.get("/api/auth/check")
async def auth_check(request: Request):
    session = await validate_session(request.cookies.get("session_token"))
    if not session:
        return Response(status_code=401)
    if session.get("status") != "approved":
        return Response(
            status_code=403,
            headers={"X-Auth-User": session["email"]}
        )
    return Response(
        status_code=200,
        headers={
            "X-Auth-User": session["email"],
            "X-Auth-Role": session.get("role", "user")
        }
    )
```

Once the FastAPI `/api/auth/check` endpoint returns 200/401/403 properly, the protected routes will work as designed.

## Concerns

1. **Rate limiting removed:** The `limit_req_zone` directive was removed because it requires `http` context. If rate limiting on `/api/auth/` is desired, either add it to `/etc/nginx/nginx.conf` in the `http {}` block, or implement rate limiting in the FastAPI service (recommended).

2. **MinIO proxy Host header:** The `/edu-files/` and `/api/curriculum/` locations use `proxy_set_header Host 127.0.0.1:9000;`. If MinIO uses virtual-host-style bucket addressing, this may need to be adjusted.

3. **aaPanel interference:** Since aaPanel manages the vhost directory, any changes made through the aaPanel UI could overwrite this config. Consider documenting this or adding a comment at the top of the file warning against manual edits via aaPanel.

4. **Cookie forwarding:** The internal auth-check subrequest forwards cookies via `proxy_set_header Cookie $http_cookie;`. Ensure the FastAPI service validates session tokens from cookies securely.
