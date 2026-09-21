"""Async SMTP email service for sending approval notifications."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings

log = logging.getLogger(__name__)


def _approval_html(full_name: str, login_url: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0fdf4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0fdf4;padding:40px 20px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;border:1px solid #d1fae5;box-shadow:0 4px 24px rgba(0,0,0,0.06);overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#059669,#10b981);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">MABDC CHATGPT</h1>
            <p style="margin:8px 0 0;color:#d1fae5;font-size:14px;">Your account has been approved</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px;">
            <p style="margin:0 0 16px;color:#1f2937;font-size:16px;line-height:1.6;">
              Hello <strong>{full_name}</strong>,
            </p>
            <p style="margin:0 0 24px;color:#6b7280;font-size:15px;line-height:1.6;">
              Great news! Your account request for <strong>MABDC CHATGPT</strong> has been reviewed and approved by an administrator. You can now sign in and start using the platform.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td align="center" style="padding:8px 0 24px;">
                  <a href="{login_url}" style="display:inline-block;background:#059669;color:#ffffff;text-decoration:none;padding:14px 40px;border-radius:10px;font-size:16px;font-weight:600;letter-spacing:0.3px;box-shadow:0 2px 8px rgba(5,150,105,0.3);">
                    Sign In Now
                  </a>
                </td>
              </tr>
            </table>
            <p style="margin:0;color:#9ca3af;font-size:13px;line-height:1.5;">
              If the button doesn't work, copy and paste this link into your browser:<br>
              <a href="{login_url}" style="color:#059669;word-break:break-all;">{login_url}</a>
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:20px 40px;text-align:center;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              &copy; 2026 MABDC. All rights reserved.<br>
              This is an automated message — please do not reply.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _credit_request_html(user_name: str, user_email: str, remaining_cents: int, timestamp: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#fef2f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fef2f2;padding:40px 20px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;border:1px solid #fecaca;box-shadow:0 4px 24px rgba(0,0,0,0.06);overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#ef4444,#dc2626);padding:32px 40px;text-align:center;">
            <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">Credit Request Alert</h1>
            <p style="margin:8px 0 0;color:#fee2e2;font-size:14px;">A teacher needs more credits</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px;">
            <p style="margin:0 0 16px;color:#1f2937;font-size:16px;line-height:1.6;">
              Hello <strong>Admin</strong>,
            </p>
            <p style="margin:0 0 24px;color:#6b7280;font-size:15px;line-height:1.6;">
              The following user has exhausted their monthly credits and requested a top-up:
            </p>
            <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px;margin-bottom:24px;">
              <p style="margin:0 0 8px;"><strong>Name:</strong> {user_name}</p>
              <p style="margin:0 0 8px;"><strong>Email:</strong> {user_email}</p>
              <p style="margin:0 0 8px;"><strong>Remaining Credits:</strong> {remaining_cents}c</p>
              <p style="margin:0;"><strong>Requested At:</strong> {timestamp}</p>
            </div>
            <p style="margin:0;color:#9ca3af;font-size:13px;line-height:1.5;">
              You can adjust their allocation in the Admin Dashboard under Users > Edit > Monthly Allocation.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:20px 40px;text-align:center;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              &copy; 2026 MABDC. All rights reserved.<br>
              This is an automated alert from the School AI Platform.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_approval_email(full_name: str, to_email: str) -> None:
    """Send an account-approved notification with a login link.

    Failures are logged but never raised — a missing email must not block
    the admin approval flow.
    """
    login_url = f"{settings.app_base_url}/login"
    msg = MIMEMultipart("alternative")
    msg["From"] = f"MABDC CHATGPT <{settings.smtp_username}>"
    msg["To"] = to_email
    msg["Subject"] = "Your MABDC CHATGPT account has been approved"

    plain = (
        f"Hello {full_name},\n\n"
        "Your MABDC CHATGPT account has been approved.\n\n"
        f"Sign in here: {login_url}\n\n"
        "— MABDC Team"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_approval_html(full_name, login_url), "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            start_tls=settings.smtp_use_tls,
            timeout=15,
        )
        log.info("Approval email sent to %s", to_email)
    except Exception as exc:
        log.error("Failed to send approval email to %s: %s", to_email, exc)


async def send_credit_request_email(user_name: str, user_email: str, remaining_cents: int) -> None:
    """Send an alert to the admin when a user requests credit top-up."""
    from datetime import datetime, timezone
    
    admin_email = "admin@mabdc.ae"  # Or configurable setting
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    msg = MIMEMultipart("alternative")
    msg["From"] = f"MABDC System Alerts <{settings.smtp_username}>"
    msg["To"] = admin_email
    msg["Subject"] = f"[Credit Request] {user_name} ({user_email}) needs top-up"

    plain = (
        f"User: {user_name} ({user_email})\n"
        f"Remaining Credits: {remaining_cents}c\n"
        f"Time: {timestamp}\n\n"
        "Please review and allocate more credits via the Admin Dashboard."
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_credit_request_html(user_name, user_email, remaining_cents, timestamp), "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            start_tls=settings.smtp_use_tls,
            timeout=15,
        )
        log.info("Credit request email sent to %s for user %s", admin_email, user_email)
    except Exception as exc:
        log.error("Failed to send credit request email for %s: %s", user_email, exc)
