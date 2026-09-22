#!/usr/bin/env python3
"""Quipper Auth Microservice — FastAPI on port 18900"""
import subprocess
import json
import os
import urllib.request
import urllib.parse
from fastapi import FastAPI, Request, Response, Cookie
from fastapi.responses import JSONResponse, RedirectResponse
from typing import Optional

app = FastAPI()

HELPER_PATH = "/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py"
PYTHON_PATH = "/usr/bin/python3"
COOKIE_NAME = "quipper_session"
COOKIE_OPTS = {"httponly": True, "secure": True, "samesite": "lax", "path": "/", "domain": ".mabdc.com"}

def call_helper(cmd: str, args: dict) -> dict:
    """Call the Python auth helper via stdin/stdout JSON."""
    payload = json.dumps({"cmd": cmd, "args": args})
    result = subprocess.run(
        [PYTHON_PATH, HELPER_PATH],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "Helper failed"}
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON from helper"}

def get_session_token(request: Request) -> Optional[str]:
    """Extract session token from cookies."""
    return request.cookies.get(COOKIE_NAME)

def verify_session(token: str) -> Optional[dict]:
    """Verify a session token and return user data, or None."""
    if not token:
        return None
    result = call_helper("verify_token", {"token": token, "type": "session"})
    if result.get("ok") and result.get("data"):
        return result["data"]
    return None

# ─── AUTH CHECK (for nginx auth_request) ──────────────────────────────
@app.get("/api/auth/check")
async def auth_check(request: Request):
    """Nginx auth_request endpoint. Returns 200/401/403 — never 302.
    Checks per-app access via X-App header (quipper or phoenix)."""
    token = get_session_token(request)
    user = verify_session(token) if token else None

    if not user:
        return Response(status_code=401)

    if user.get("status") != "approved":
        return Response(status_code=403)

    # Check per-app access
    app = request.headers.get("x-app", "")
    if app == "quipper" and not user.get("access_quipper", 1):
        return Response(status_code=403)
    if app == "phoenix" and not user.get("access_phoenix", 1):
        return Response(status_code=403)

    resp = Response(status_code=200)
    resp.headers["X-Auth-User"] = user.get("email", "")
    resp.headers["X-Auth-Role"] = user.get("role", "")
    return resp

# ─── LOGIN ─────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JSONResponse({"ok": False, "error": "Email and password required"}, status_code=400)

    user_result = call_helper("get_user_by_email", {"email": email})
    if not user_result.get("ok") or not user_result.get("data"):
        return JSONResponse({"ok": False, "error": "Invalid email or password"}, status_code=401)

    user = user_result["data"]

    check = call_helper("verify_password", {"password": password, "hash": user["password_hash"]})
    if not check.get("ok") or not check.get("data", {}).get("valid"):
        return JSONResponse({"ok": False, "error": "Invalid email or password"}, status_code=401)

    if user["status"] == "pending":
        return JSONResponse({"ok": False, "error": "Account pending approval"}, status_code=403)
    if user["status"] == "revoked":
        return JSONResponse({"ok": False, "error": "Account has been revoked"}, status_code=403)

    call_helper("delete_tokens_for_user", {"user_id": user["id"], "type": "session"})
    tok = call_helper("create_token", {"user_id": user["id"], "type": "session", "ttl": 604800})
    if not tok.get("ok"):
        return JSONResponse({"ok": False, "error": "Failed to create session"}, status_code=500)

    resp = JSONResponse({"ok": True, "data": {"role": user["role"], "email": user["email"]}})
    resp.set_cookie(COOKIE_NAME, tok["data"]["token"], max_age=604800, **COOKIE_OPTS)
    return resp

# ─── SIGNUP ────────────────────────────────────────────────────────────
@app.post("/api/auth/signup")
async def signup(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JSONResponse({"ok": False, "error": "Email and password required"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"ok": False, "error": "Password must be at least 6 characters"}, status_code=400)

    existing = call_helper("get_user_by_email", {"email": email})
    if existing.get("ok") and existing.get("data"):
        return JSONResponse({"ok": False, "error": "Email already registered"}, status_code=409)

    result = call_helper("create_user", {"email": email, "password": password})
    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": result.get("error", "Registration failed")}, status_code=400)

    call_helper("send_email", {
        "to": "sottodennis@gmail.com",
        "subject": f"New Quipper access request from {email}",
        "body": f"A new user has signed up for Quipper access.\n\nEmail: {email}\n\nPlease log in to the admin dashboard to approve or reject.\nhttps://library.mabdc.com/auth/admin.html"
    })

    return JSONResponse({"ok": True})

# ─── RESET REQUEST ─────────────────────────────────────────────────────
@app.post("/api/auth/reset-request")
async def reset_request(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()

    if not email:
        return JSONResponse({"ok": False, "error": "Email required"}, status_code=400)

    user_result = call_helper("get_user_by_email", {"email": email})
    if user_result.get("ok") and user_result.get("data"):
        user = user_result["data"]
        call_helper("delete_tokens_for_user", {"user_id": user["id"], "type": "reset"})
        tok = call_helper("create_token", {"user_id": user["id"], "type": "reset", "ttl": 3600})
        if tok.get("ok"):
            reset_url = f"https://library.mabdc.com/auth/reset.html?token={tok['data']['token']}"
            call_helper("send_email", {
                "to": email,
                "subject": "Reset your Quipper password",
                "body": f"Click the link below to reset your password. This link expires in 1 hour.\n\n{reset_url}\n\nIf you did not request this, ignore this email."
            })

    return JSONResponse({"ok": True, "message": "If that email exists, a reset link has been sent."})

# ─── RESET PASSWORD ────────────────────────────────────────────────────
@app.post("/api/auth/reset")
async def reset_password(request: Request):
    body = await request.json()
    token = body.get("token", "")
    password = body.get("password", "")

    if not token or not password:
        return JSONResponse({"ok": False, "error": "Token and password required"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"ok": False, "error": "Password must be at least 6 characters"}, status_code=400)

    result = call_helper("verify_token", {"token": token, "type": "reset"})
    if not result.get("ok") or not result.get("data"):
        return JSONResponse({"ok": False, "error": "Invalid or expired reset token"}, status_code=400)

    call_helper("update_password", {"user_id": result["data"]["user_id"], "password": password})
    call_helper("delete_tokens_for_user", {"user_id": result["data"]["user_id"], "type": "reset"})

    return JSONResponse({"ok": True})

# ─── LOGOUT ────────────────────────────────────────────────────────────
@app.post("/api/auth/logout")
@app.get("/api/auth/logout")
async def logout(request: Request):
    token = get_session_token(request)
    user = verify_session(token) if token else None
    if user:
        call_helper("delete_tokens_for_user", {"user_id": user["user_id"], "type": "session"})

    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/", domain=".mabdc.com", samesite="lax", secure=True)
    return resp

# ─── CURRENT USER ACCESS FLAGS ─────────────────────────────────────────
@app.get("/api/auth/my-access")
async def my_access(request: Request):
    """Return the current user's per-app access flags for the landing page."""
    token = get_session_token(request)
    user = verify_session(token) if token else None
    if not user or user.get("status") != "approved":
        return Response(status_code=401)
    return JSONResponse({
        "ok": True,
        "data": {
            "quipper": bool(user.get("access_quipper", 1)),
            "phoenix": bool(user.get("access_phoenix", 1))
        }
    })

# ─── PHOENIX AUTO-LOGIN ────────────────────────────────────────────────
@app.get("/api/auth/phoenix-session")
async def phoenix_session(request: Request):
    """Perform server-side login to ebooks.mabdc.com and return the session cookie.
    Called by nginx via auth_request subrequest or directly by the landing page."""
    token = get_session_token(request)
    user = verify_session(token) if token else None
    if not user or user.get("status") != "approved":
        return Response(status_code=401)

    try:
        import ssl
        data = urllib.parse.urlencode({
            "httpd_username": "mabdc",
            "httpd_password": "mabdc",
            "httpd_location": "/"
        }).encode()
        req = urllib.request.Request("https://ebooks.mabdc.com/do-login", data=data, method="POST")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Use a custom handler that does NOT follow redirects
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
            def http_error_302(self, req, fp, code, msg, headers):
                return fp
        opener = urllib.request.build_opener(
            NoRedirectHandler,
            urllib.request.HTTPSHandler(context=ctx)
        )
        resp = opener.open(req, timeout=10)
        # Extract set-cookie from response headers
        cookies = resp.headers.get_all("Set-Cookie") or []
        for cookie in cookies:
            if "ebook_session=" in cookie:
                session_val = cookie.split("ebook_session=")[1].split(";")[0]
                body = json.dumps({"ok": True, "session": session_val}).encode()
                # Use raw ASGI response to bypass Starlette's cookie quoting
                from starlette.responses import Response as StarletteResponse
                raw_resp = StarletteResponse(content=body, media_type="application/json")
                # Append Set-Cookie directly to the raw headers list (bypasses MutableHeaders sanitization)
                raw_resp.raw_headers.append(
                    (b"set-cookie", f"ebook_session={session_val}; HttpOnly; Max-Age=86400; Path=/phoenix/; SameSite=lax; Secure".encode())
                )
                return raw_resp
        return JSONResponse({"ok": False, "error": "No ebook_session in response"}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

# ─── ADMIN: SET PER-APP ACCESS ─────────────────────────────────────────
@app.post("/api/admin/set-access")
async def admin_set_access(request: Request):
    token = get_session_token(request)
    user = verify_session(token)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    if user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)

    body = await request.json()
    uid = body.get("id")
    app = body.get("app")
    enabled = body.get("enabled")
    if not uid or not app or enabled is None:
        return JSONResponse({"ok": False, "error": "id, app, and enabled required"}, status_code=400)

    result = call_helper("set_access", {"id": uid, "app": app, "enabled": bool(enabled)})
    return JSONResponse(result)

# ─── ADMIN ENDPOINTS ───────────────────────────────────────────────────
@app.get("/api/admin/users")
async def admin_users(request: Request):
    token = get_session_token(request)
    user = verify_session(token)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    if user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)

    result = call_helper("list_users", {})
    return JSONResponse(result)

@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    token = get_session_token(request)
    user = verify_session(token)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    if user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)

    result = call_helper("get_stats", {})
    return JSONResponse(result)

@app.post("/api/admin/approve")
async def admin_approve(request: Request):
    token = get_session_token(request)
    user = verify_session(token)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    if user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)

    body = await request.json()
    uid = body.get("id")
    if not uid:
        return JSONResponse({"ok": False, "error": "User ID required"}, status_code=400)

    call_helper("approve_user", {"id": uid})
    target = call_helper("get_user_by_id", {"id": uid})
    if target.get("ok") and target.get("data"):
        call_helper("send_email", {
            "to": target["data"]["email"],
            "subject": "Your Quipper account has been approved",
            "body": "Your account has been approved by an administrator. You can now log in at https://library.mabdc.com/auth/login.html"
        })

    return JSONResponse({"ok": True})

@app.post("/api/admin/revoke")
async def admin_revoke(request: Request):
    token = get_session_token(request)
    user = verify_session(token)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    if user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "Admin access required"}, status_code=403)

    body = await request.json()
    uid = body.get("id")
    if not uid:
        return JSONResponse({"ok": False, "error": "User ID required"}, status_code=400)

    target = call_helper("get_user_by_id", {"id": uid})
    call_helper("revoke_user", {"id": uid})
    if target.get("ok") and target.get("data"):
        call_helper("send_email", {
            "to": target["data"]["email"],
            "subject": "Your Quipper access has been revoked",
            "body": "Your Quipper account access has been revoked by an administrator. Contact admin@mabdc.ae if you believe this was a mistake."
        })

    return JSONResponse({"ok": True})
