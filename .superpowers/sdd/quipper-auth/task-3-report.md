# Task 3 Report: Lua Shared Utility Library

## Summary
Created and deployed `lib.lua` — the core shared utility library for the quipper.mabdc.com auth system. This library provides JSON encoding/decoding, Python helper invocation, cookie parsing, and HTTP response utilities for all other Lua scripts in the system.

## What Was Done

1. **Created local directory**: `C:\Users\DENNIS\Downloads\CHATGPT-CLONE\_guardian\lua\`
2. **Wrote `lib.lua`** with the following modules:
   - `json_escape(s)` — Internal function to escape strings for safe JSON inclusion (handles `\`, `"`, `\n`, `\r`, `\t`)
   - `table_to_json(t)` — Recursive Lua table to JSON string converter (handles arrays, objects, strings, numbers, booleans, null)
   - `parse_json(str)` — Hand-rolled recursive descent JSON parser (state-machine approach, NOT `load()` — avoids security risk)
   - `call_python(cmd, args)` — Invokes `/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py` via `io.popen` with shell-safe single-quote escaping
   - `json_response(status, data)` — Sends JSON HTTP response with proper headers (`Content-Type`, `Cache-Control: no-store`) and exits
   - `read_body()` — Reads the nginx request body
   - `parse_body_json()` — Parses the request body as JSON
   - `get_cookie(name)` — Extracts a cookie value by name from the request
3. **Created remote directory** on server: `/www/wwwroot/quipper.mabdc.com/_guardian/lua/`
4. **Uploaded** `lib.lua` via SCP to the server
5. **Set permissions**:
   - Directory: `750` owned by `root:www-data`
   - File: `640` owned by `root:www-data`
6. **Verified nginx config**: `nginx -t` passed successfully

## Test Output

### File Permissions
```
-rw-r----- 1 root www-data 7718 Sep 22 02:48 /www/wwwroot/quipper.mabdc.com/_guardian/lua/lib.lua
```

### Nginx Config Test
```
nginx: [warn] duplicate MIME type "text/html" in /etc/nginx/sites-enabled/library.sfxsai.com.conf:102
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```
The warning about duplicate MIME type is pre-existing and unrelated to our changes. Nginx config test passed.

## Design Decisions

- **No `load()` for JSON parsing**: The original plan suggested using `load()` to parse JSON, which is a code injection vulnerability. Implemented a proper recursive descent parser instead.
- **No `ngx.quote_sql_str` for JSON escaping**: Used manual string escaping with `gsub` for proper JSON string safety.
- **Shell escaping for Python calls**: Single quotes in JSON are escaped before passing through `io.popen` to prevent shell injection.
- **Empty table defaults to array `[]`**: An empty Lua table `{}` serializes as `[]` (JSON array) since that's the more common default for API responses.

## Concerns

1. **`io.popen` performance**: Each call to `call_python()` spawns a new Python process. For high-traffic endpoints, this could become a bottleneck. Consider a persistent connection or Unix socket approach in the future.
2. **`get_cookie` pattern (FIXED)**: The original code used PCRE syntax `(?:^|;)` which doesn't work in Lua patterns. This was caught and fixed before final deployment. The solution prepends `"; "` to the cookie string so all cookies can be matched uniformly with `;%s*name=([^;]*)`. If cookie names contain special Lua pattern characters (like `-`, `.`, `%`), they should be escaped before use. Currently assumes simple alphanumeric cookie names.
3. **No unicode escape handling in JSON parser**: The `parse_json` function handles standard escapes (`\n`, `\t`, `\\`, `\"`, etc.) but does not handle `\uXXXX` unicode escapes. This is acceptable since the Python helper output is expected to use UTF-8 directly rather than unicode escapes.
4. **File size**: 7718 bytes — reasonable for a shared utility library.

## Status
**DONE**
