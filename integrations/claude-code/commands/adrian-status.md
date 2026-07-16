---
description: Show Adrian security status - backend URL, key presence, and connection health.
---

Report the current Adrian configuration and whether it can reach the backend.
NEVER print, `cat`, or `echo` the API key value or the `.env` contents.

1. Show the effective backend URL and whether a key is set (boolean only):
```
grep -E '^ADRIAN_WS_URL=' ~/.adrian/.env 2>/dev/null || echo 'ADRIAN_WS_URL not set (default ws://localhost:8080/ws)'
grep -Eq '^ADRIAN_API_KEY=adr_(live|local)_.+' ~/.adrian/.env && echo 'API key: set' || echo 'API key: NOT set'
```

2. Check connectivity + auth (prints OK/FAIL + backend mode, never the key):
```
adrian-python -m adrian_cc.agent verify
```
Report its output verbatim.

If the key is missing or `verify` FAILs, suggest running `/adrian-cc:adrian-init`.
