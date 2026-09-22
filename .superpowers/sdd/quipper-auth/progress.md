# SDD ledger — plan: quipper-auth-plan.md

## Pre-flight scan

| Task Pair | Interface | Finding |
|-----------|-----------|--------|
| T1 → T4,T5,T6,T7 | Python helper JSON protocol | Clean — all tasks use `{"cmd":"...","args":{...}}` stdin/stdout consistently |
| T3 → T4,T5,T6,T7,T8 | Lua `lib.lua` require path | Clean — all use `require('_guardian.lua.lib')` matching lua_package_path |
| T4 → T8 | Session cookie name `quipper_session` | Clean — same name in login setter and access_gate reader |
| T5 → T7 | User status values (pending/approved/revoked) | Clean — consistent string literals across signup, admin, gate |
| T6 → T4 | Token type strings (session/reset) | Clean — distinct types, no collision |
| T8 (router) → T4,T5,T6,T7 | URI routing patterns | Clean — exact match for auth endpoints, prefix match for admin |
| T9 → T8 | HTML pages call `/api/auth/*` and `/api/admin/*` | Clean — matches router patterns exactly |
| T10 → T1-T9 | Nginx vhost location blocks | Clean — `/auth/` public, `/api/` routed to Lua, everything else gated |

### Internal task consistency checks

| Task | Check | Result |
|------|-------|--------|
| T1 | Python commands dict covers all spec operations | ✅ All 15 commands present |
| T3 | `table_to_json` handles nested objects for `create_token` args | ⚠️ Plan uses `['type']` Lua syntax — needs verification that table_to_json handles non-string keys correctly |
| T4 | Login flow calls verify_password then create_token | ✅ Matches spec |
| T7 | Admin endpoint verifies role == 'admin' | ✅ Present in first lines |
| T8 | access_gate allows /auth/ and /api/ through | ✅ Early return pattern |
| T10 | Rate limit zone name matches usage | ✅ `quipper_auth` defined and used |

### Rulings

**Ruling: T3 `['type']` Lua syntax** — The plan uses `{..., ['type'] = 'session', ...}` which is valid Lua for reserved-word keys. The `table_to_json` function iterates with `pairs()` and calls `tostring(k)` on keys, which produces the string `"type"`. This works correctly. No change needed. Cost if wrong: Python helper receives malformed JSON, all token operations fail.

No blocking conflicts found. Proceeding to execution.

**Ruling: Task 1 permission fix** — Changed `_guardian/` directory to `775 root:www-data` and `auth_helper.py` to `750 root:www-data` so nginx worker (www-data) can execute the script and SQLite WAL mode can write journal files. Cost if wrong: all API endpoints return 500 on database write operations.

---

Task 1: complete (deployed via SCP, review clean)
Task 2: complete (deployed via SCP, review clean — fixed error logging + temp file cleanup)
Task 3: complete (deployed via SCP, review clean — replaced load() JSON parser with recursive descent)
Task 4-8 (Lua endpoints): SCRAPPED — nginx 1.18.0 lacks lua-nginx-module
Task 4-8 (replacement): FastAPI auth microservice on port 18900 + auth_request — COMPLETE
Task 9: complete (deployed via SCP, review clean)
Task 10: complete (nginx vhost deployed, auth_request wired, end-to-end verified)

## Architecture Pivot

**Ruling: Nginx Lua → Python FastAPI + auth_request** — Server's nginx 1.18.0 (Ubuntu package) has no lua-nginx-module compiled in. Pivoted to FastAPI microservice on 127.0.0.1:18900 with nginx `auth_request` for access gating. All Lua files remain on disk but are unused. Cost if wrong: ~2 hours rework.

**Ruling: auth_check status codes** — FastAPI `/api/auth/check` initially returned 302 redirects, but nginx `auth_request` only understands 200/401/403. Fixed to return 401 (unauthenticated) and 403 (pending), with nginx `error_page` named locations handling redirects. Cost if wrong: entire site returns 500.

**Ruling: Cloudflare Flexible SSL redirect loop** — Port 80 `return 301 https://...` caused infinite loop because CF connects to origin via HTTP. Fixed by merging port 80 and 443 into single server block. Cost if wrong: site completely inaccessible (ERR_TOO_MANY_REDIRECTS).

**Ruling: Force HTTPS in auth redirects** — Behind CF Flexible, `$scheme` is `http`, so nginx generated `http://` redirect URLs that would drop `Secure` cookies. Fixed by hardcoding `https://$host/...` in error_page handlers. Cost if wrong: silent login failure as browser drops Secure cookie on HTTP redirect.

## Final Verification (through Cloudflare)

All flows tested end-to-end via real Cloudflare path:
- Unauthenticated → 302 redirect to https://quipper.mabdc.com/auth/login.html ✅
- Auth pages publicly accessible → 200 ✅
- Login sets Secure cookie → 200 + Set-Cookie ✅
- Authenticated SPA access → 200 ✅
- Admin API protected by auth_request → 200 + valid JSON ✅
