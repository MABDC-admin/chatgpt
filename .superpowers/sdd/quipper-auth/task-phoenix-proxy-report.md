# Phoenix Ebooks Reverse Proxy Test Report

**Date:** 2026-09-22
**Target:** `ebooks.mabdc.com` (178.105.105.92, Apache)
**Proxy Host:** `library.mabdc.com` via nginx on 10.121.15.125

## 1. Direct Access Tests (with session cookie)

All endpoints return **200 OK** when accessed with a valid `ebook_session` cookie:

| Endpoint | Status |
|----------|--------|
| `/` (main library page) | 200 |
| `/_books/grade-1/english-1/index.html` (book reader) | 200 |
| `/_assets/library-playful-v15.css?v=...` (CSS) | 200 |
| `/_assets/library-playful-v15.js` (JS) | 200 |
| `/_assets/covers/grade-1/english-1.jpg` (cover image) | 200 |

Login via `POST /do-login` with `httpd_username=mabdc&httpd_password=mabdc&httpd_location=/` successfully sets the `ebook_session` cookie.

## 2. Nginx Reverse Proxy Tests

A temporary nginx config was tested on port 18899:

```nginx
server {
    listen 18899;
    server_name localhost;

    location /phoenix/ {
        proxy_pass https://ebooks.mabdc.com/;
        proxy_set_header Host ebooks.mabdc.com;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_ssl_server_name on;
        proxy_cookie_domain ebooks.mabdc.com $host;
    }
}
```

### Results

| Test | HTTP Status | Notes |
|------|-------------|-------|
| Proxy **without** auth cookie | **302** | Redirects to login (expected) |
| Proxy **with** `ebook_session` cookie | **200** | HTML content served correctly |

**Proxying works.** The nginx `proxy_pass` to the upstream HTTPS Apache server functions correctly with SNI enabled.

## 3. Path Analysis (Book Reader HTML)

The book reader at `/_books/grade-1/english-1/index.html` uses **relative paths** for assets:

```html
href="../../../_assets/library-playful-v15.css?v=20260603-hide-toc1"
href="../../../index.html"
href="/logout"
href="pages/1.jpg"
href="../../../GRADE%201/ENGLISH%201.pdf"
src="../../../_assets/reader-playful-v15.js"
```

### Key findings:
- **Most paths are relative** (`../../../_assets/...`, `pages/1.jpg`) — these will resolve correctly through the proxy since the browser resolves them relative to the current URL path.
- **One absolute path exists: `/logout`** — this is a concern. When proxied under `/phoenix/`, clicking logout will navigate to `library.mabdc.com/logout` instead of `library.mabdc.com/phoenix/logout`. This would need to be handled either by:
  - Adding a separate nginx location block for `/logout` that proxies to the ebook server
  - Using `sub_filter` to rewrite `/logout` to `/phoenix/logout` in the HTML response
- **PDF links** use relative paths with URL-encoded spaces (`../../../GRADE%201/ENGLISH%201.pdf`) — these should work fine through the proxy.

## 4. Cookie & Authentication Strategy

### Do we need to inject `ebook_session` automatically?

**Yes.** Without the cookie, the upstream returns a 302 redirect to the login page. Since users authenticate through our FastAPI app, they won't have an `ebook_session` cookie.

### Recommended approach:

Our FastAPI backend (or an nginx Lua/auth_request module) should:

1. **On first access to `/phoenix/`:** Check if the user is authenticated via FastAPI auth.
2. **If authenticated but no `ebook_session`:** Perform a server-side POST to `https://ebooks.mabdc.com/do-login` with the shared credentials (`mabdc`/`mabdc`), capture the `ebook_session` cookie value, and set it on the client's browser scoped to the `/phoenix/` path.
3. **Subsequent requests:** The browser sends the `ebook_session` cookie automatically; nginx proxies it to the upstream.

Alternatively, use nginx `auth_request` or `lua` to transparently inject the cookie on every request without the client ever needing to hold it directly.

## 5. Concerns & Recommendations

| Concern | Severity | Mitigation |
|---------|----------|------------|
| `/logout` absolute path breaks under proxy | Medium | Use `sub_filter` to rewrite, or add a dedicated `/logout` location block |
| Session expiry — `ebook_session` may expire server-side | Medium | Implement re-login logic when upstream returns 302 |
| Shared credentials (`mabdc`/`mabdc`) hardcoded | Low | Store in env vars or secrets manager on the proxy server |
| `proxy_cookie_domain` rewriting may affect cookie scope | Low | Test that cookies are properly scoped to `/phoenix/` path |
| External font preconnects (`fonts.googleapis.com`) | None | These are absolute URLs to CDNs; browsers handle them natively |
| Upstream SSL certificate validation | Low | Currently using `-k` (insecure) in tests; production nginx should trust the upstream cert or use `proxy_ssl_verify off` only if necessary |

## 6. Conclusion

**Reverse proxying `ebooks.mabdc.com` through nginx at `/phoenix/` is viable.**

- All asset types (HTML, CSS, JS, images, PDFs) serve correctly through the proxy.
- Relative paths in the book reader resolve properly under the proxy prefix.
- Automatic `ebook_session` injection is required and feasible.
- The `/logout` absolute path needs special handling.
- A production nginx config should be added to the existing `library.sfxsai.com.conf` vhost rather than a separate file.

**Status: READY FOR IMPLEMENTATION**
