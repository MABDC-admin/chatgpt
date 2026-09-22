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
            approved_at TEXT,
            access_quipper INTEGER DEFAULT 1,
            access_phoenix INTEGER DEFAULT 1
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
    # Auto-migrate: add columns if they don't exist on older DBs
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "access_quipper" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN access_quipper INTEGER DEFAULT 1")
    if "access_phoenix" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN access_phoenix INTEGER DEFAULT 1")
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
    rows = conn.execute("SELECT id, email, status, role, created_at, approved_at, access_quipper, access_phoenix FROM users ORDER BY created_at DESC").fetchall()
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

def set_access(uid, app, enabled):
    """Toggle per-app access. app must be 'quipper' or 'phoenix'."""
    if app not in ("quipper", "phoenix"):
        return {"ok": False, "error": "Invalid app"}
    col = f"access_{app}"
    val = 1 if enabled else 0
    conn = get_db()
    conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (val, uid))
    # Re-read current state
    row = conn.execute("SELECT status, access_quipper, access_phoenix FROM users WHERE id = ?", (uid,)).fetchone()
    if row:
        if not enabled and row["access_quipper"] == 0 and row["access_phoenix"] == 0:
            # Both disabled → revoke
            conn.execute("UPDATE users SET status = 'revoked' WHERE id = ?", (uid,))
            conn.execute("DELETE FROM tokens WHERE user_id = ?", (uid,))
        elif enabled and row["status"] == "revoked":
            # Re-enabling access for a revoked user → restore to approved
            conn.execute("UPDATE users SET status = 'approved', approved_at = datetime('now') WHERE id = ?", (uid,))
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
        "SELECT t.*, u.email, u.status, u.role, u.access_quipper, u.access_phoenix FROM tokens t JOIN users u ON t.user_id = u.id WHERE t.token_hash = ? AND t.type = ?",
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

def send_email(to, subject, body):
    import tempfile
    import subprocess
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(body)
            f.flush()
            tmp_path = f.name
        result = subprocess.run(
            ['/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh', to, subject, tmp_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr or result.stdout}
        return {"ok": True}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

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
    "set_access": lambda args: set_access(args["id"], args["app"], args["enabled"]),
    "create_token": lambda args: create_token(args["user_id"], args["type"], args["ttl"]),
    "verify_token": lambda args: verify_token(args["token"], args["type"]),
    "delete_tokens_for_user": lambda args: delete_tokens_for_user(args["user_id"], args.get("type")),
    "update_password": lambda args: update_password(args["user_id"], args["password"]),
    "seed_admin": lambda args: seed_admin(args["email"], args["password"]),
    "get_stats": lambda args: get_stats(),
    "send_email": lambda args: send_email(args["to"], args["subject"], args["body"]),
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
