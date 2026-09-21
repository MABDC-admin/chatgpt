"""Turn provider and internal exceptions into safe, useful user-facing text.

Raw exception strings were being interpolated straight into API responses and
SSE error frames. Those strings can carry request URLs, header fragments,
org identifiers and key prefixes from the provider SDK -- none of which a
teacher or student should see, and some of which should not leave the server
at all. The full detail still goes to the server log for diagnosis.
"""

import logging
import uuid

log = logging.getLogger("uvicorn.error")

_FRIENDLY = {
    "rate_limit": "The AI service is busy right now. Try again in a moment.",
    "timeout": "The AI service took too long to respond. Try again.",
    "auth": "The AI service rejected our credentials. An administrator needs to check the API key.",
    "content": "The request was blocked by the AI provider's content filter. Try rephrasing.",
    "quota": "The AI account has run out of credit. An administrator needs to top it up.",
}


_FRIENDLY["bad_request"] = "The AI service rejected the request parameters. An administrator should check the server logs."


def _classify(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "rate" in text and "limit" in text:
        return _FRIENDLY["rate_limit"]
    if "timeout" in text or "timed out" in text:
        return _FRIENDLY["timeout"]
    if "authentication" in text or "api key" in text or "401" in text:
        return _FRIENDLY["auth"]
    if "content_policy" in text or "content filter" in text or "safety" in text:
        return _FRIENDLY["content"]
    if "quota" in text or "insufficient_quota" in text or "billing" in text:
        return _FRIENDLY["quota"]
    if "badrequesterror" in text or "invalid_request_error" in text or "unknown parameter" in text:
        return _FRIENDLY["bad_request"]
    return ""


def safe_message(exc: Exception, *, action: str) -> str:
    """Log the real error; return something safe to show a user.

    The returned text carries a short reference id so a user can quote it and
    an administrator can find the matching line in the log.
    """
    ref = uuid.uuid4().hex[:8]
    log.error("[%s] %s failed: %s: %s", ref, action, type(exc).__name__, exc, exc_info=True)

    friendly = _classify(exc)
    if friendly:
        return f"{friendly} (ref {ref})"
    return f"{action} could not be completed. Quote reference {ref} to your administrator."
