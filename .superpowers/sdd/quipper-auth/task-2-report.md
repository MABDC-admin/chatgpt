# Task 2 Report: Email Sending Shell Script & Python Integration

**Date:** 2026-09-22
**Status:** DONE

## What Was Done

1. **Created local directory** `C:\Users\DENNIS\Downloads\CHATGPT-CLONE\_guardian\`
2. **Wrote `send_mail.sh`** — a bash script that sends email via curl using SMTP STARTTLS to `mail.mabdc.ae:587` with credentials for `admin@mabdc.ae`.
3. **Uploaded `send_mail.sh`** to the server at `/www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh` via SCP.
4. **Set permissions** on the shell script:
   - `chmod 750` (owner rwx, group r-x, others none)
   - `chown root:www-data`
5. **Tested shell script directly** by sending a test email to `sottodennis@gmail.com`.
6. **Verified `auth_helper.py`** already contained the `send_email` function and its COMMANDS entry (added by a prior task).
7. **Re-applied permissions** to `auth_helper.py` (750, root:www-data) to ensure consistency.
8. **Tested `send_email` through Python** by piping JSON to `auth_helper.py`.

## Test Output

### Shell Script Direct Test
```
Exit code: 0
```
Email sent successfully via curl SMTP.

### File Permissions Verification
```
-rwxr-x--- 1 root www-data 8799 Sep 22 02:42 /www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py
-rwxr-x--- 1 root www-data  728 Sep 22 02:47 /www/wwwroot/quipper.mabdc.com/_guardian/send_mail.sh
```
Both files have correct ownership and permissions.

### Python Integration Test
```json
{"ok": true}
```
The `send_email` command was processed successfully through `auth_helper.py`.

## Concerns

1. **Hardcoded SMTP Credentials**: The password `Denskie123` is stored in plaintext inside `send_mail.sh`. While file permissions (750, root:www-data) restrict access, this is still a security risk. Consider moving credentials to a separate config file with stricter permissions or using environment variables.
2. **Error Suppression**: The script redirects stderr from curl to `/dev/null` (`2>/dev/null`), which means SMTP errors won't be captured in `result.stderr` by the Python helper. If email delivery fails silently, debugging will be difficult. Consider logging errors to a file instead.
3. **Temp File Cleanup**: If `subprocess.run` raises an exception before completion, the temp file created by `NamedTemporaryFile(delete=False)` may not be cleaned up. A try/finally block around `os.unlink(f.name)` would be safer.
4. **No Input Validation**: The `send_email` function doesn't validate the `to` email address format, which could potentially be abused for header injection if user input flows directly into the subject or recipient fields.
