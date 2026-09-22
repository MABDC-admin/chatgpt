# Quipper Subpath Deployment Report

**Date:** 2026-09-22
**Task:** Prepare Quipper SPA to be served from `/quipper/` subpath on `library.mabdc.com`
**Status:** DONE

## 1. File Copy

Copied all files from `/www/wwwroot/quipper.mabdc.com/` to `/www/wwwroot/library.mabdc.com/quipper/` using rsync, excluding `_guardian` and `auth` directories.

**Verification output:**
```
Files copied
assets
backups
css
docs
favicon.svg
gdrive-minio-map.json
index.html
js
robots.txt
vite.svg
...
```

Ownership set to `root:www-data` recursively.

## 2. index.html Path Rewriting

Applied sed replacements to prefix all local absolute paths with `/quipper/`. External CDN URLs were left untouched.

**Verification output (src/href attributes after changes):**
```
href="/quipper/favicon.svg"
src="/quipper/js/quipper-naturalsort.js"
src="/quipper/assets/index-CILA2bQ7.js"
href="/quipper/assets/index-BCcMZQf5.css"
href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css"       ← CDN (unchanged)
src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"          ← CDN (unchanged)
src="/quipper/js/quipper-math.js"
href="/quipper/css/mobile-fix.css?v=20260310c"
href="/quipper/css/reskin.css?v=20260310g"
href="/quipper/css/interactive.css?v=1773160774"
href="#root"                                                                ← anchor (unchanged)
src="/quipper/js/quipper-toolbox.js"
src="/quipper/js/pdf-thumbnails.js"
src="/quipper/js/mobile-fix.js?v=20260310c"
src="/quipper/js/reskin.js?v=20260310g"
```

All local asset paths correctly prefixed. CDN links preserved.

## 3. JS Bundle API Path Analysis

Scanned the JS bundle (`assets/*.js`) for hardcoded API paths:

| Pattern | Occurrences | Notes |
|---|---|---|
| `/api/curriculum` | 1 match (unique path) | Found in JS bundle |
| `/edu-files` | 0 matches | Not present |
| `/api/` | 1 file matched | Only in the main assets JS bundle |

**No changes made to JS files.** The `/api/curriculum` path should remain as-is because it will be handled by nginx location blocks that proxy `/api/` requests to the appropriate backend.

## Concerns & Notes

1. **JS runtime fetch calls:** The JS bundle contains 1 reference to `/api/curriculum`. This will resolve to `library.mabdc.com/api/curriculum` at runtime. The nginx config for `library.mabdc.com` must have a `location /api/` block that proxies to the correct backend, otherwise these API calls will 404.

2. **Vite base path:** The app was built with Vite using the default base of `/`. If the app uses dynamic imports or lazy-loaded chunks, those may also use absolute paths. Since only one JS bundle file exists (`index-CILA2bQ7.js`), this is likely not an issue, but should be verified during testing.

3. **CSS url() references:** If any CSS files contain absolute `url(/...)` references (e.g., fonts, images), those would also need rewriting. The sed commands only targeted `index.html`.

4. **Client-side routing:** If the SPA uses client-side routing (e.g., Vue Router in history mode), navigation to `/quipper/some-route` requires nginx to serve `index.html` for all routes under `/quipper/`.

5. **Backup files:** Multiple backup copies of `index.html` exist in the target directory (`.bak`, `.bak2`, `.bak3`, etc.). These were copied over but don't affect functionality.
