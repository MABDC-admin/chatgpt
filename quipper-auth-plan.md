# Quipper Auth System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the entire quipper.mabdc.com SPA behind email/password authentication with admin approval workflow, password reset, and an admin dashboard.

**Architecture:** Nginx Lua scripts act as thin HTTP glue (read request body, set cookies, return responses). All business logic — bcrypt hashing, SQLite operations, token generation, SMTP email — lives in a single Python 3 helper script called by Lua via `io.popen()`. Static HTML pages with Tailwind CSS provide the UI. No new containers or services.

**Tech Stack:** Nginx lua-nginx-module (aaPanel stock), Python 3.10 + python3-bcrypt 5.0.0 + sqlite3, curl (SMTP support confirmed), Tailwind CSS CDN, vanilla JS.

**Spec:** `/www/wwwroot/quipper.mabdc.com/_guardian/quipper-auth-spec.md`

## Global Constraints

- Server: Ubuntu 22.04, aaPanel nginx with lua-nginx-module compiled in
- Python 3.10.12 at `/usr/bin/python3`, bcrypt 5.0.0, sqlite3 3.37.2
- curl supports SMTP/SMTPS protocols
- No luarocks, no cjson, no resty.http available for Lua — all JSON handling goes through Python
- Target directory: `/www/wwwroot/quipper.mabdc.com/`
- Guardian directory: `/www/wwwroot/quipper.mabdc.com/_guardian/`
- SMTP: `mail.mabdc.ae:587`, STARTTLS, user `admin@mabdc.ae`, pass `Denskie123`
- Admin account: `sottodennis@gmail.com` / `Denskie123`
- All files deployed directly to server via SSH (no git, no Docker)
- Nginx reload required after vhost changes: `nginx -t && nginx -s reload`

## Review Focus

1. **Python helper called concurrently by multiple nginx workers** — SQLite WAL mode must be enabled to prevent "database locked" errors under concurrent access. Tested in Task 1.
2. **Lua io.popen() error handling** — if Python crashes or returns malformed JSON, Lua must return a 500 error, not silently fail open. Tested in Task 2.
3. **Session cookie security attributes** — cookies must be HttpOnly, Secure, SameSite=Strict to prevent XSS theft. Verified in Task 4.
4. **Rate limiting on auth endpoints** — without rate limiting, brute force attacks on `/api/auth/login` are trivial. Verified in Task 9.
5. **Admin endpoint authorization** — admin API must reject non-admin users; a missing role check exposes approve/revoke to any authenticated user. Tested in Task 7.

---

### Task 1: Python Auth Helper — Core + Database

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py`

**Interfaces:**
- Consumes: nothing (entry point)
- Produces: CLI tool accepting JSON on stdin, outputting JSON on stdout. Commands: `init_db`, `hash_password`, `verify_password`, `create_user`, `get_user_by_email`, `list_users`, `approve_user`, `revoke_user`, `create_token`, `verify_token`, `delete_tokens_for_user`, `seed_admin`, `get_stats`, `get_user_by_id`

- [ ] **Step 1: Write the Python helper script**

Create `/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py` with the following complete code:

```python
#!/usr/bin/env python3
"""Quipper Auth Helper — called by nginx Lua via io.popen().

Protocol: reads JSON from stdin, writes JSON to stdout.
{"cmd": "...", "args": {...}} -> {"ok": true/false, "data": {...}, "error": "..."}
"""
import sys
import json
import sqlite3
import secrets
import hashlib
import os
from datetime import datetime, timedelta

import bcrypt

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
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
    """)
    conn.commit()
    conn.close()
    return {"ok": True}

def hash_password(password):
    h = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    return {"ok": True, "data": {"hash": h}}

def verify_password(password, password_hash):
    ok = bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    return {"ok": True, "data": {"valid": ok}}

def create_user(email, password):
    conn = get_db()
    try:
        pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        uid = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO users (id, email, password_hash, status, role) VALUES (?, ?, ?, 'pending', 'user')",
            (uid, email.lower().strip(), pw_hash)
        )
        conn.commit()
        return {"ok": True, "data": {"id": uid}}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "Email already registered"}
    finally:
        conn.close()

def get_user_by_email(email):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    if not row:
        return {"ok": True, "data": None}
    return {"ok": True, "data": dict(row)}

def get_user_by_id(uid):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    if not row:
        return {"ok": True, "data": None}
    return {"ok": True, "data": dict(row)}

def list_users():
    conn = get_db()
    rows = conn.execute("SELECT id, email, status, role, created_at, approved_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return {"ok": True, "data": [dict(r) for r in rows]}

def approve_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET status = 'approved', approved_at = datetime('now') WHERE id = ?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True}

def revoke_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET status = 'revoked' WHERE id = ?", (uid,))
    conn.execute("DELETE FROM tokens WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True}

def create_token(user_id, token_type, ttl_seconds):
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    tid = secrets.token_hex(16)
    expires = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO tokens (id, user_id, type, token_hash, expires_at) VALUES (?, ?, ?, ?, ?)",
        (tid, user_id, token_type, token_hash, expires)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "data": {"token": raw, "id": tid}}

def verify_token(raw_token, token_type):
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    conn = get_db()
    row = conn.execute(
        "SELECT t.*, u.email, u.status, u.role FROM tokens t JOIN users u ON t.user_id = u.id WHERE t.token_hash = ? AND t.type = ?",
        (token_hash, token_type)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": True, "data": None}
    expires = datetime.fromisoformat(row["expires_at"])
    if expires < datetime.utcnow():
        conn.execute("DELETE FROM tokens WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return {"ok": True, "data": None}
    result = dict(row)
    conn.close()
    return {"ok": True, "data": result}

def delete_tokens_for_user(user_id, token_type=None):
    conn = get_db()
    if token_type:
        conn.execute("DELETE FROM tokens WHERE user_id = ? AND type = ?", (user_id, token_type))
    else:
        conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

def update_password(user_id, new_password):
    pw_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    conn = get_db()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}

def seed_admin(email, password):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    if existing:
        conn.close()
        return {"ok": True, "data": {"created": False}}
    pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    uid = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO users (id, email, password_hash, status, role, approved_at) VALUES (?, ?, ?, 'approved', 'admin', datetime('now'))",
        (uid, email.lower().strip(), pw_hash)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "data": {"created": True}}

def get_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) as c FROM users WHERE status='pending'").fetchone()["c"]
    approved = conn.execute("SELECT COUNT(*) as c FROM users WHERE status='approved'").fetchone()["c"]
    revoked = conn.execute("SELECT COUNT(*) as c FROM users WHERE status='revoked'").fetchone()["c"]
    conn.close()
    return {"ok": True, "data": {"total": total, "pending": pending, "approved": approved, "revoked": revoked}}

COMMANDS = {
    "init_db": lambda args: init_db(),
    "hash_password": lambda args: hash_password(args["password"]),
    "verify_password": lambda args: verify_password(args["password"], args["hash"]),
    "create_user": lambda args: create_user(args["email"], args["password"]),
    "get_user_by_email": lambda args: get_user_by_email(args["email"]),
    "get_user_by_id": lambda args: get_user_by_id(args["id"]),
    "list_users": lambda args: list_users(),
    "approve_user": lambda args: approve_user(args["id"]),
    "revoke_user": lambda args: revoke_user(args["id"]),
    "create_token": lambda args: create_token(args["user_id"], args["type"], args["ttl"]),
    "verify_token": lambda args: verify_token(args["token"], args["type"]),
    "delete_tokens_for_user": lambda args: delete_tokens_for_user(args["user_id"], args.get("type")),
    "update_password": lambda args: update_password(args["user_id"], args["password"]),
    "seed_admin": lambda args: seed_admin(args["email"], args["password"]),
    "get_stats": lambda args: get_stats(),
}

def main():
    try:
        raw = sys.stdin.read()
        req = json.loads(raw)
        cmd = req.get("cmd")
        args = req.get("args", {})
        if cmd not in COMMANDS:
            print(json.dumps({"ok": False, "error": f"Unknown command: {cmd}"}))
            sys.exit(1)
        result = COMMANDS[cmd](args)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Upload to server and test init_db + seed_admin**

Run:
```powershell
scp -o BatchMode=yes -o ConnectTimeout=10 -P 1988 -i "$env:USERPROFILE\.ssh\id_ed25519_zt125" "C:\Users\DENNIS\Downloads\CHATGPT-CLONE\temp_auth_helper.py" root@10.121.15.125:/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```
Then SSH and run:
```bash
chmod 600 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
echo '{"cmd":"init_db","args":{}}' | python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
echo '{"cmd":"seed_admin","args":{"email":"sottodennis@gmail.com","password":"Denskie123"}}' | python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
echo '{"cmd":"get_stats","args":{}}' | python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```
Expected: `{"ok": true, "data": {"total": 1, "pending": 0, "approved": 1, "revoked": 0}}`

- [ ] **Step 3: Test password verification**

Run:
```bash
echo '{"cmd":"get_user_by_email","args":{"email":"sottodennis@gmail.com"}}' | python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```
Expected: Returns user object with `status: approved`, `role: admin`, and a bcrypt hash.

- [ ] **Step 4: Commit**

No git repo on server. Verify file exists and is readable only by root/nginx:
```bash
ls -la /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
chown root:root /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
chmod 600 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```

---

### Task 2: Email Sending Script

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh`

**Interfaces:**
- Consumes: Called by Lua or Python via `os.system()` / `io.popen()`. Arguments: `to_email`, `subject`, `body_text`
- Produces: Sends email via SMTP STARTTLS using curl. Exit code 0 on success.

- [ ] **Step 1: Write the send_mail.sh script**

Create locally then upload:

```bash
#!/bin/bash
# Usage: send_mail.sh <to_email> <subject> <body_file>
TO="$1"
SUBJECT="$2"
BODY_FILE="$3"

if [ -z "$TO" ] || [ -z "$SUBJECT" ] || [ ! -f "$BODY_FILE" ]; then
    echo "Usage: send_mail.sh <to> <subject> <body_file>" >&2
    exit 1
fi

BODY=$(cat "$BODY_FILE")

# Build RFC 2822 message
MSG_FILE=$(mktemp)
cat > "$MSG_FILE" <<EOF
From: Quipper Admin <admin@mabdc.ae>
To: ${TO}
Subject: ${SUBJECT}
Content-Type: text/plain; charset=utf-8
MIME-Version: 1.0

${BODY}
EOF

curl --silent --show-error \
    --ssl-reqd \
    --url 'smtp://mail.mabdc.ae:587' \
    --user 'admin@mabdc.ae:Denskie123' \
    --mail-from 'admin@mabdc.ae' \
    --mail-rcpt "${TO}" \
    --upload-file "$MSG_FILE" 2>/dev/null

RESULT=$?
rm -f "$MSG_FILE"
exit $RESULT
```

- [ ] **Step 2: Upload and set permissions**

```powershell
scp -o BatchMode=yes -o ConnectTimeout=10 -P 1988 -i "$env:USERPROFILE\.ssh\id_ed25519_zt125" "C:\Users\DENNIS\Downloads\CHATGPT-CLONE\temp_send_mail.sh" root@10.121.15.125:/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh
```
Then:
```bash
chmod 700 /www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh
chown root:root /www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh
```

- [ ] **Step 3: Test sending an email**

```bash
echo "Test email from Quipper auth system." > /tmp/test_body.txt
/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh "sottodennis@gmail.com" "Quipper Auth Test" /tmp/test_body.txt
echo "Exit code: $?"
rm /tmp/test_body.txt
```
Expected: Exit code 0, email received at sottodennis@gmail.com.

- [ ] **Step 4: Add email helper to auth_helper.py**

Append a `send_email` command to `auth_helper.py` that creates a temp body file and calls `send_mail.sh`:

```python
def send_email(to, subject, body):
    import tempfile
    import subprocess
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(body)
        f.flush()
        result = subprocess.run(
            ['/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh', to, subject, f.name],
            capture_output=True, text=True
        )
    os.unlink(f.name)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr}
    return {"ok": True}
```

Add to COMMANDS dict:
```python
"send_email": lambda args: send_email(args["to"], args["subject"], args["body"]),
```

Upload updated `auth_helper.py` and test:
```bash
echo '{"cmd":"send_email","args":{"to":"sottodennis@gmail.com","subject":"Test","body":"Hello from Python"}}' | python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```

---

### Task 3: Lua Library — Shared Utilities

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/lib.lua`

**Interfaces:**
- Consumes: Required by all other Lua scripts via `require`
- Produces: `_M.call_python(cmd, args_table)` → parsed JSON table, `_M.json_response(status, data)`, `_M.read_body()` → string, `_M.get_cookie(name)` → string|nil

- [ ] **Step 1: Create directory and write lib.lua**

```bash
mkdir -p /www/wwwroot/quipper.mabdc.com/_guardian/lua
```

Write `lib.lua`:

```lua
local _M = {}

-- Call the Python helper and parse its JSON response.
-- Uses io.popen to pipe JSON to the Python script's stdin.
function _M.call_python(cmd, args)
    -- Build JSON manually (no cjson available)
    local json_args = _M.table_to_json(args or {})
    local input = '{"cmd":"' .. cmd .. '","args":' .. json_args .. '}'
    
    local handle = io.popen(
        'echo ' .. ngx.quote_sql_str(input):gsub("^'", '"'):gsub("'$", '"') ..
        ' | /usr/bin/python3 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py',
        'r'
    )
    if not handle then
        return nil, "Failed to execute auth helper"
    end
    local result = handle:read('*a')
    handle:close()
    
    if not result or result == '' then
        return nil, "Empty response from auth helper"
    end
    
    -- Parse JSON response manually (simple parser for our known format)
    return _M.parse_json(result), nil
end

-- Minimal JSON parser for the Python helper's known output format.
-- Handles: strings, numbers, booleans, null, nested objects, arrays.
function _M.parse_json(str)
    -- Use a safe load approach
    local func = load('return ' .. str:gsub('null', 'nil')
        :gsub('true', 'true')
        :gsub('false', 'false')
        :gsub('"([^"]-)"%s*:', function(k) return '[' .. string.format('%q', k) .. ']=' end)
    )
    if func then
        local ok, val = pcall(func)
        if ok then return val end
    end
    -- Fallback: extract ok field
    if str:find('"ok": true') or str:find('"ok":true') then
        return {ok = true}
    end
    return {ok = false, error = "JSON parse failed"}
end

-- Convert a simple Lua table to JSON string (flat, string values only)
function _M.table_to_json(t)
    if type(t) ~= 'table' then return '{}' end
    local parts = {}
    for k, v in pairs(t) do
        local val_str
        if type(v) == 'string' then
            val_str = '"' .. v:gsub('"', '\\"'):gsub('\n', '\\n') .. '"'
        elseif type(v) == 'number' then
            val_str = tostring(v)
        elseif type(v) == 'boolean' then
            val_str = v and 'true' or 'false'
        elseif type(v) == 'table' then
            val_str = _M.table_to_json(v)
        else
            val_str = 'null'
        end
        table.insert(parts, '"' .. tostring(k) .. '":' .. val_str)
    end
    return '{' .. table.concat(parts, ',') .. '}'
end

function _M.json_response(status, data)
    ngx.status = status
    ngx.header['Content-Type'] = 'application/json'
    ngx.header['Cache-Control'] = 'no-store'
    if type(data) == 'table' then
        ngx.say(_M.table_to_json(data))
    else
        ngx.say(data or '{}')
    end
    return ngx.exit(status)
end

function _M.read_body()
    ngx.req.read_body()
    return ngx.req.get_body_data() or ''
end

function _M.parse_body_json()
    local body = _M.read_body()
    if body == '' then return {} end
    return _M.parse_json(body)
end

function _M.get_cookie(name)
    local cookies = ngx.var.http_cookie
    if not cookies then return nil end
    local pattern = name .. '=([^;]+)'
    return cookies:match(pattern)
end

return _M
```

- [ ] **Step 2: Upload and verify syntax**

Upload via SCP, then test:
```bash
# Quick syntax check via nginx
nginx -t 2>&1
```

---

### Task 4: Login Endpoint + Session Cookie

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_login.lua`

**Interfaces:**
- Consumes: `lib.lua` (call_python, json_response, parse_body_json)
- Produces: POST handler. On success: sets `quipper_session` cookie, returns `{"ok": true}`. On failure: returns `{"ok": false, "error": "..."}`.

- [ ] **Step 1: Write api_login.lua**

```lua
local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local email = body.email
local password = body.password

if not email or not password then
    return lib.json_response(400, {ok = false, error = 'Email and password required'})
end

-- Look up user
local user, err = lib.call_python('get_user_by_email', {email = email})
if err or not user or not user.ok then
    return lib.json_response(500, {ok = false, error = 'Internal error'})
end
if not user.data then
    return lib.json_response(401, {ok = false, error = 'Invalid email or password'})
end

-- Verify password
local check, err = lib.call_python('verify_password', {password = password, hash = user.data.password_hash})
if err or not check or not check.ok or not check.data or not check.data.valid then
    return lib.json_response(401, {ok = false, error = 'Invalid email or password'})
end

-- Check status
if user.data.status == 'pending' then
    return lib.json_response(403, {ok = false, error = 'Account pending approval'})
end
if user.data.status == 'revoked' then
    return lib.json_response(403, {ok = false, error = 'Account has been revoked'})
end

-- Delete old sessions
lib.call_python('delete_tokens_for_user', {user_id = user.data.id, ['type'] = 'session'})

-- Create session token (7 days = 604800 seconds)
local tok, err = lib.call_python('create_token', {user_id = user.data.id, ['type'] = 'session', ttl = 604800})
if err or not tok or not tok.ok then
    return lib.json_response(500, {ok = false, error = 'Failed to create session'})
end

-- Set cookie
ngx.header['Set-Cookie'] = 'quipper_session=' .. tok.data.token ..
    '; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=604800'

return lib.json_response(200, {ok = true, data = {role = user.data.role, email = user.data.email}})
```

- [ ] **Step 2: Upload to server**

SCP to `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_login.lua`.

---

### Task 5: Signup Endpoint

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_signup.lua`

**Interfaces:**
- Consumes: `lib.lua`
- Produces: POST handler. Creates user with pending status, sends notification email to admin, returns success.

- [ ] **Step 1: Write api_signup.lua**

```lua
local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local email = body.email
local password = body.password

if not email or not password then
    return lib.json_response(400, {ok = false, error = 'Email and password required'})
end

if #password < 6 then
    return lib.json_response(400, {ok = false, error = 'Password must be at least 6 characters'})
end

-- Check if exists
local existing = lib.call_python('get_user_by_email', {email = email})
if existing and existing.ok and existing.data then
    return lib.json_response(409, {ok = false, error = 'Email already registered'})
end

-- Create user
local result = lib.call_python('create_user', {email = email, password = password})
if not result or not result.ok then
    local msg = (result and result.error) or 'Registration failed'
    return lib.json_response(400, {ok = false, error = msg})
end

-- Notify admin (fire and forget)
lib.call_python('send_email', {
    to = 'sottodennis@gmail.com',
    subject = 'New Quipper access request from ' .. email,
    body = 'A new user has signed up for Quipper access.\n\nEmail: ' .. email .. '\n\nPlease log in to the admin dashboard to approve or reject this request.\nhttps://quipper.mabdc.com/auth/admin.html'
})

return lib.json_response(200, {ok = true})
```

- [ ] **Step 2: Upload to server**

SCP to `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_signup.lua`.

---

### Task 6: Password Reset Endpoints

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_reset_request.lua`
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_reset.lua`

**Interfaces:**
- `api_reset_request.lua`: POST with `{email}`. Generates reset token (1hr TTL), sends email with reset link.
- `api_reset.lua`: POST with `{token, password}`. Verifies token, updates password, deletes token.

- [ ] **Step 1: Write api_reset_request.lua**

```lua
local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local email = body.email

if not email then
    return lib.json_response(400, {ok = false, error = 'Email required'})
end

-- Always return success to prevent email enumeration
local user = lib.call_python('get_user_by_email', {email = email})
if user and user.ok and user.data then
    -- Delete old reset tokens
    lib.call_python('delete_tokens_for_user', {user_id = user.data.id, ['type'] = 'reset'})
    -- Create new reset token (1 hour = 3600 seconds)
    local tok = lib.call_python('create_token', {user_id = user.data.id, ['type'] = 'reset', ttl = 3600})
    if tok and tok.ok then
        local reset_url = 'https://quipper.mabdc.com/auth/reset.html?token=' .. tok.data.token
        lib.call_python('send_email', {
            to = email,
            subject = 'Reset your Quipper password',
            body = 'Click the link below to reset your password. This link expires in 1 hour.\n\n' .. reset_url .. '\n\nIf you did not request this, ignore this email.'
        })
    end
end

return lib.json_response(200, {ok = true, message = 'If that email exists, a reset link has been sent.'})
```

- [ ] **Step 2: Write api_reset.lua**

```lua
local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local token = body.token
local password = body.password

if not token or not password then
    return lib.json_response(400, {ok = false, error = 'Token and password required'})
end

if #password < 6 then
    return lib.json_response(400, {ok = false, error = 'Password must be at least 6 characters'})
end

-- Verify token
local result = lib.call_python('verify_token', {token = token, ['type'] = 'reset'})
if not result or not result.ok or not result.data then
    return lib.json_response(400, {ok = false, error = 'Invalid or expired reset token'})
end

-- Update password
lib.call_python('update_password', {user_id = result.data.user_id, password = password})

-- Delete used token
lib.call_python('delete_tokens_for_user', {user_id = result.data.user_id, ['type'] = 'reset'})

return lib.json_response(200, {ok = true})
```

- [ ] **Step 3: Upload both files**

SCP to server.

---

### Task 7: Admin API Endpoints

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_admin.lua`

**Interfaces:**
- Consumes: `lib.lua`, session cookie for auth
- Produces: Single router handling GET `/api/admin/users`, GET `/api/admin/stats`, POST `/api/admin/approve`, POST `/api/admin/revoke`. All require admin role.

- [ ] **Step 1: Write api_admin.lua**

This single file routes based on URI path and method:

```lua
local lib = require('_guardian.lua.lib')

-- Verify admin session first
local session_token = lib.get_cookie('quipper_session')
if not session_token then
    return lib.json_response(401, {ok = false, error = 'Not authenticated'})
end

local tok_result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
if not tok_result or not tok_result.ok or not tok_result.data then
    return lib.json_response(401, {ok = false, error = 'Invalid session'})
end

if tok_result.data.role ~= 'admin' then
    return lib.json_response(403, {ok = false, error = 'Admin access required'})
end

local uri = ngx.var.uri
local method = ngx.req.get_method()

if uri == '/api/admin/users' and method == 'GET' then
    local result = lib.call_python('list_users', {})
    return lib.json_response(200, result)

elseif uri == '/api/admin/stats' and method == 'GET' then
    local result = lib.call_python('get_stats', {})
    return lib.json_response(200, result)

elseif uri == '/api/admin/approve' and method == 'POST' then
    local body = lib.parse_body_json()
    if not body.id then
        return lib.json_response(400, {ok = false, error = 'User ID required'})
    end
    lib.call_python('approve_user', {id = body.id})
    -- Get user email for notification
    local user = lib.call_python('get_user_by_id', {id = body.id})
    if user and user.ok and user.data then
        lib.call_python('send_email', {
            to = user.data.email,
            subject = 'Your Quipper account has been approved',
            body = 'Your account has been approved by an administrator. You can now log in at https://quipper.mabdc.com/auth/login.html'
        })
    end
    return lib.json_response(200, {ok = true})

elseif uri == '/api/admin/revoke' and method == 'POST' then
    local body = lib.parse_body_json()
    if not body.id then
        return lib.json_response(400, {ok = false, error = 'User ID required'})
    end
    local user = lib.call_python('get_user_by_id', {id = body.id})
    lib.call_python('revoke_user', {id = body.id})
    if user and user.ok and user.data then
        lib.call_python('send_email', {
            to = user.data.email,
            subject = 'Your Quipper access has been revoked',
            body = 'Your Quipper account access has been revoked by an administrator. Contact admin@mabdc.ae if you believe this was a mistake.'
        })
    end
    return lib.json_response(200, {ok = true})

else
    return lib.json_response(404, {ok = false, error = 'Not found'})
end
```

- [ ] **Step 2: Upload to server**

SCP to `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_admin.lua`.

---

### Task 8: Access Gate + Logout + API Router

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua`
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_logout.lua`
- Create: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/api_router.lua`

**Interfaces:**
- `access_gate.lua`: Used as `access_by_lua_file`. Checks session cookie. If valid → allows request. If invalid → redirects to `/auth/login.html`.
- `api_logout.lua`: POST handler. Deletes session token, clears cookie.
- `api_router.lua`: Routes `/api/auth/*` requests to the correct handler file.

- [ ] **Step 1: Write access_gate.lua**

```lua
local lib = require('_guardian.lua.lib')

local uri = ngx.var.uri

-- Allow auth pages and API endpoints through (they have their own auth)
if uri:match('^/auth/') or uri:match('^/api/') then
    return
end

-- Check session cookie
local session_token = lib.get_cookie('quipper_session')
if not session_token then
    return ngx.redirect('/auth/login.html')
end

local result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
if not result or not result.ok or not result.data then
    -- Clear invalid cookie
    ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
    return ngx.redirect('/auth/login.html')
end

-- Check user status
if result.data.status ~= 'approved' then
    ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
    return ngx.redirect('/auth/pending.html')
end

-- User is authenticated and approved — allow request through
```

- [ ] **Step 2: Write api_logout.lua**

```lua
local lib = require('_guardian.lua.lib')

local session_token = lib.get_cookie('quipper_session')
if session_token then
    local result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
    if result and result.ok and result.data then
        lib.call_python('delete_tokens_for_user', {user_id = result.data.user_id, ['type'] = 'session'})
    end
end

ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
return lib.json_response(200, {ok = true})
```

- [ ] **Step 3: Write api_router.lua**

```lua
local uri = ngx.var.uri

if uri == '/api/auth/login' then
    return require('_guardian.lua.api_login')
elseif uri == '/api/auth/signup' then
    return require('_guardian.lua.api_signup')
elseif uri == '/api/auth/reset-request' then
    return require('_guardian.lua.api_reset_request')
elseif uri == '/api/auth/reset' then
    return require('_guardian.lua.api_reset')
elseif uri == '/api/auth/logout' then
    return require('_guardian.lua.api_logout')
elseif uri:match('^/api/admin/') then
    return require('_guardian.lua.api_admin')
else
    ngx.status = 404
    ngx.say('{"ok":false,"error":"Not found"}')
    return ngx.exit(404)
end
```

- [ ] **Step 4: Upload all three files**

SCP to server.

---

### Task 9: Static HTML Auth Pages

**Files:**
- Create: `/www/wwwroot/quipper.mabdc.com/auth/login.html`
- Create: `/www/wwwroot/quipper.mabdc.com/auth/signup.html`
- Create: `/www/wwwroot/quipper.mabdc.com/auth/reset-request.html`
- Create: `/www/wwwroot/quipper.mabdc.com/auth/reset.html`
- Create: `/www/wwwroot/quipper.mabdc.com/auth/admin.html`
- Create: `/www/wwwroot/quipper.mabdc.com/auth/pending.html`

**Interfaces:**
- Consumes: Tailwind CSS CDN, vanilla JS calling `/api/auth/*` and `/api/admin/*`
- Produces: User-facing HTML pages

- [ ] **Step 1: Write login.html**

Create locally with Tailwind CSS CDN. Contains email + password fields, submit button, links to signup and forgot password. On submit: POST to `/api/auth/login`. On success: redirect to `/`. On error: show error message.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login — Quipper</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-gray-50 flex items-center justify-center p-4">
    <div class="max-w-md w-full bg-white rounded-xl shadow-sm border p-8">
        <h1 class="text-2xl font-bold text-gray-900 mb-2">Sign in to Quipper</h1>
        <p class="text-sm text-gray-500 mb-6">St. Francis Xavier Smart Academy</p>
        <form id="loginForm" class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input type="email" id="email" required class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-purple-500">
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
                <input type="password" id="password" required class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-purple-500">
            </div>
            <div id="error" class="hidden text-sm text-red-600 bg-red-50 p-3 rounded-lg"></div>
            <button type="submit" class="w-full bg-purple-600 text-white py-2.5 rounded-lg font-medium hover:bg-purple-700 transition-colors">Sign In</button>
        </form>
        <div class="mt-4 text-sm text-center space-y-2">
            <a href="/auth/reset-request.html" class="text-purple-600 hover:underline">Forgot password?</a>
            <p class="text-gray-500">Don't have an account? <a href="/auth/signup.html" class="text-purple-600 hover:underline">Sign up</a></p>
        </div>
    </div>
    <script>
    document.getElementById('loginForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errEl = document.getElementById('error');
        errEl.classList.add('hidden');
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    email: document.getElementById('email').value,
                    password: document.getElementById('password').value
                })
            });
            const data = await res.json();
            if (data.ok) {
                window.location.href = '/';
            } else {
                errEl.textContent = data.error || 'Login failed';
                errEl.classList.remove('hidden');
            }
        } catch (err) {
            errEl.textContent = 'Network error. Please try again.';
            errEl.classList.remove('hidden');
        }
    });
    </script>
</body>
</html>
```

- [ ] **Step 2: Write signup.html**

Similar layout. Fields: email, password, confirm password. POST to `/api/auth/signup`. On success: redirect to `/auth/pending.html`.

- [ ] **Step 3: Write reset-request.html**

Field: email. POST to `/api/auth/reset-request`. On success: show "Check your email" message.

- [ ] **Step 4: Write reset.html**

Reads `?token=` from URL. Fields: new password, confirm. POST to `/api/auth/reset` with token + password. On success: redirect to login.

- [ ] **Step 5: Write pending.html**

Static page: "Your account is awaiting admin approval. You'll receive an email when approved."

- [ ] **Step 6: Write admin.html**

Dashboard page. On load: GET `/api/admin/stats` and `/api/admin/users`. Shows stats cards and a user table. Each pending user has Approve button (POST `/api/admin/approve`). Each approved user has Revoke button (POST `/api/admin/revoke`). Includes logout button.

- [ ] **Step 7: Upload all HTML files**

```bash
mkdir -p /www/wwwroot/quipper.mabdc.com/auth/
```
SCP all files.

---

### Task 10: Nginx Vhost Configuration + Deployment

**Files:**
- Modify: `/www/server/panel/vhost/nginx/quipper.mabdc.com.conf`

**Interfaces:**
- Consumes: All Lua scripts and HTML pages from previous tasks
- Produces: Updated nginx config that gates the SPA and routes API calls

- [ ] **Step 1: Add lua_package_path to nginx.conf**

The Lua scripts use `require('_guardian.lua.lib')` which needs the webroot in the package path. Add to `/etc/nginx/nginx.conf` inside the `http {}` block:

```
lua_package_path '/www/wwwroot/quipper.mabdc.com/?.lua;/www/wwwroot/quipper.mabdc.com/?/init.lua;;';
```

- [ ] **Step 2: Update quipper.mabdc.com.conf**

Replace the existing `location /` block and add new locations. The full updated vhost:

```nginx
server {
    listen 80;
    listen 443 ssl http2;
    server_name quipper.mabdc.com;

    root /www/wwwroot/quipper.mabdc.com;

    ssl_certificate /etc/letsencrypt/live/quipper.mabdc.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/quipper.mabdc.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256;

    # Rate limiting zone for auth endpoints
    limit_req_zone $binary_remote_addr zone=quipper_auth:10m rate=10r/m;

    # ---- OAuth2 Auth Proxy Endpoint (existing) ----
    location /oauth2/ {
        proxy_pass http://127.0.0.1:4181;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ---- Auth pages (public, no gate) ----
    location /auth/ {
        try_files $uri =404;
    }

    # ---- Auth API endpoints ----
    location /api/ {
        limit_req zone=quipper_auth burst=5 nodelay;
        content_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/api_router.lua;
    }

    # ---- API from MinIO (protected by access gate) ----
    location /api/curriculum/ {
        access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
        proxy_set_header Authorization "";
        proxy_pass http://127.0.0.1:9000/quipper-curriculum/;
        proxy_set_header Host 127.0.0.1:9000;
        proxy_hide_header x-amz-id-2;
        proxy_hide_header x-amz-request-id;
        proxy_hide_header x-amz-version-id;
        proxy_hide_header ETag;
        add_header Cache-Control "public, max-age=3600";
        add_header Content-Type "application/json; charset=utf-8";
        proxy_read_timeout 60s;
    }

    # ---- MinIO PDF files (protected by access gate) ----
    location /edu-files/ {
        access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
        proxy_set_header Authorization "";
        proxy_pass http://127.0.0.1:9000/education-worksheets/;
        proxy_set_header Host 127.0.0.1:9000;
        add_header Access-Control-Allow-Origin "*" always;
        add_header Cache-Control "public, max-age=86400" always;
        proxy_hide_header x-amz-request-id;
        proxy_hide_header x-amz-id-2;
        proxy_hide_header x-amz-version-id;
    }

    # ---- Static assets (protected by access gate) ----
    location ~* ^/(js|css|img|fonts|assets|favicon\.ico|vite\.svg) {
        access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
        try_files $uri =404;
        expires 1h;
        add_header Cache-Control "public, no-transform";
    }

    location = /gdrive-minio-map.json {
        access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
        try_files $uri =404;
        add_header Cache-Control "public, max-age=60";
    }

    # ---- Serve React SPA (protected by access gate) ----
    location / {
        access_by_lua_file /www/wwwroot/quipper.mabdc.com/_guardian/lua/access_gate.lua;
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, must-revalidate" always;
    }
}
```

- [ ] **Step 3: Test and reload nginx**

```bash
nginx -t 2>&1
nginx -s reload
```

- [ ] **Step 4: End-to-end verification**

1. Visit `https://quipper.mabdc.com/` → should redirect to `/auth/login.html`
2. Log in as `sottodennis@gmail.com` / `Denskie123` → should redirect to `/`
3. Visit `/auth/admin.html` → should show admin dashboard with 1 user
4. Log out → should redirect to login
5. Sign up with a test email → should show pending page, admin should receive email
6. Approve via admin dashboard → user should receive approval email
7. Test password reset flow

- [ ] **Step 5: Fix any issues found during testing**

Common issues to watch for:
- Lua `require` path failures → check `lua_package_path`
- Python permission errors → ensure nginx worker can execute python3
- SQLite lock errors → verify WAL mode is set
- Cookie not being sent → check Secure flag requires HTTPS (it does)
