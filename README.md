# AI Private Office

A **fresh, isolated starter application**. It does not reuse legacy application code or database. It is a functional foundation, not a completed autonomous AI company.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
export CEO_PASSWORD='use-a-long-private-password'
export SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export COOKIE_SECURE=false  # only for local HTTP testing
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000.

## Included now
- Clean white responsive Executive HQ and login-first navigation
- Private session-based login with environment-configured CEO password
- AI staff configuration and hiring, departments and tool *requests* (no actual integrations)
- Task creation, assignment, status updates and audit trail
- Live desks reflecting recorded tasks (not actual AI execution)
- Learning source registry for YouTube URLs, website URLs and notes (not ingestion)
- Honest placeholder screens for research, finance, integrations and governance

## NOT implemented
- AI model calls or autonomous work; real YouTube transcription; PDF/video ingestion; MFA; account roles; production-grade security controls; OAuth/social APIs; finance ledger; automatic sync.
- SQLite is suitable for local prototype only. For staging/production, migrate to managed PostgreSQL, secure cookies/HTTPS, implement MFA, CSRF protection, rate limiting, migrations, backups, audit retention, secrets management, and proper user identities.

## Isolation / deployment
Do not deploy over the existing GitHub/Railway service or attach to its existing database. Create a new repository or branch and **separate service and database** after review. Do not deploy to production without explicit CEO approval.

## Important security notes
Session cookie is signed, HttpOnly and Secure by default. For localhost HTTP only, set COOKIE_SECURE=false. Password login currently uses one environment secret and has no rate limiting or MFA: **do not expose this prototype publicly**. Forms are not CSRF-protected. No external AI or social media action is performed.
