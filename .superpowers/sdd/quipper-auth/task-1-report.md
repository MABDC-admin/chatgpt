# Task 1 Report: Python Auth Helper — Core + Database

**Date:** 2026-09-22
**Status:** DONE

## What Was Done

1. **Read the plan** at `C:\Users\DENNIS\Downloads\CHATGPT-CLONE\quipper-auth-plan.md` (Task 1, lines 36–306).

2. **Wrote `auth_helper.py`** to `C:\Users\DENNIS\Downloads\CHATGPT-CLONE\_guardian\auth_helper.py` with all 16 commands:
   - `init_db`, `hash_password`, `verify_password`, `create_user`, `get_user_by_email`, `get_user_by_id`, `list_users`, `approve_user`, `revoke_user`, `create_token`, `verify_token`, `delete_tokens_for_user`, `update_password`, `seed_admin`, `get_stats`, `send_email`
   - SQLite WAL mode enabled for concurrent access
   - JSON stdin/stdout protocol as specified

3. **Uploaded to server** via SCP to `root@10.121.15.125:/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py`

4. **Set permissions:**
   - `chmod 600` — readable/writable only by root
   - `chown root:root`
   - Verified: `-rw------- 1 root root 8799 Sep 22 02:42`

5. **Ran all verification commands** over SSH.

## Test Output

### init_db
```json
{"ok": true}
```
✅ PASS — Database and tables created successfully.

### seed_admin
```json
{"ok": true, "data": {"created": true}}
```
✅ PASS — Admin user created with approved status.

### get_stats
```json
{"ok": true, "data": {"total": 1, "pending": 0, "approved": 1, "revoked": 0}}
```
✅ PASS — Exactly matches expected output.

### get_user_by_email
```json
{"ok": true, "data": {"id": "6b99e4266c41d21d48dce30c337c37bb", "email": "sottodennis@gmail.com", "password_hash": "$2b$12$/4v33NMrsXGwUjX2.CTxAOhNaxM6COcrmZafPEz3f1BWs5/MiQVla", "status": "approved", "role": "admin", "created_at": "2026-09-22 02:43:02", "approved_at": "2026-09-22 02:43:02"}}
```
✅ PASS — User object returned with `status: "approved"`, `role: "admin"`, valid bcrypt hash (`$2b$12$...`).

### File permissions
```
-rw------- 1 root root 8799 Sep 22 02:42 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
```
✅ PASS — Only root can read/write. Not world-readable.

## Concerns & Notes

1. **Permission issue for nginx workers:** The file is `600 root:root`. Nginx worker processes typically run as `www` or `www-data`. When Lua scripts call this Python helper via `io.popen()`, the nginx worker needs **execute** permission on the file. This will likely need to be changed to `700` with ownership `root:www` or `750` before Task 2+ Lua integration. Flagging for the parent session.

2. **`datetime.utcnow()` deprecation:** Python 3.12+ deprecates `datetime.utcnow()`. Server runs 3.10.12 so this is fine currently, but worth noting for future upgrades.

3. **`send_email` depends on `send_mail.sh`:** The `send_email` command references `/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh` which doesn't exist yet (created in Task 2). Calling `send_email` before Task 2 will return an error. This is expected per the plan's task ordering.

4. **Database location:** `auth.db` is created in the same `_guardian/` directory. With `600` permissions on the script, the db file inherits default umask. Should verify db file permissions after Task 2 testing.

5. **No input validation on token TTL:** `create_token` accepts `ttl_seconds` directly without bounds checking. A malicious caller could set extremely large values. Low risk since only Lua scripts call this, not end users directly.
