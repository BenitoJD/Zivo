---
name: security-review
description: Use this skill when adding authentication, handling user input, working with secrets, creating API endpoints, or implementing sensitive features in this FastAPI + Next.js repo. Provides a repo-aligned security checklist and review patterns.
---

# Security Review Skill

This skill helps review code changes for real security risk in this repo's
FastAPI backend, Next.js frontend, PostgreSQL data layer, and infra surface.

## When to Activate

- Adding or changing authentication or authorization
- Handling user input, uploads, or webhook payloads
- Creating new API endpoints in `backend/app/api/`
- Working with secrets, credentials, or third-party APIs
- Changing admin flows or Firebase custom-claim behavior
- Adding background jobs, external integrations, or payment-like flows
- Changing CORS, cookies, session, or rate-limiting behavior

## Repo Security Model

Keep these repo-specific assumptions in mind during review:

- Backend is FastAPI
- Frontend is Next.js; API calls go through the helpers in `frontend/lib/`
- Admin access is controlled with Firebase custom claims
- Secrets belong in local `.env` or AWS Secrets Manager, not in source
- PostgreSQL access should go through SQLAlchemy or parameterized SQL
- Production work is confirmation-gated and should stay narrowly scoped

## Security Checklist

### 1. Secrets Management

#### ❌ Never Do This

```python
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
DATABASE_URL = "YOUR_DATABASE_URL"
```

#### ✅ Always Do This

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    database_url: str


settings = Settings()
```

#### Verification Steps

- [ ] No hardcoded API keys, tokens, passwords, or service-account blobs
- [ ] Secrets loaded from env or secret manager
- [ ] `env.local.defaults` documents required variables without real values
- [ ] No secrets in logs, tests, fixtures, or committed artifacts
- [ ] Startup validation fails clearly when required secrets are missing

### 2. Input Validation

#### Validate at the Boundary

```python
from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
```

```python
@router.post("/users")
async def create_user(payload: CreateUserRequest):
    return await user_service.create_user(payload)
```

#### File Upload Validation

```python
from fastapi import HTTPException, UploadFile, status


ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 5 * 1024 * 1024


async def validate_upload(file: UploadFile) -> None:
    content = await file.read()

    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    await file.seek(0)
```

#### Verification Steps

- [ ] All request bodies, params, query values, and headers validated
- [ ] Validation uses whitelist/schema rules, not ad hoc string checks
- [ ] File uploads restrict size and type
- [ ] External webhook payloads are authenticated and validated
- [ ] Error responses do not leak internals

### 3. SQL Injection Prevention

#### ❌ Never Concatenate SQL

```python
query = f"SELECT * FROM users WHERE email = '{email}'"
session.execute(query)
```

#### ✅ Always Use SQLAlchemy or Bound Parameters

```python
from sqlalchemy import text


session.execute(
    text("SELECT * FROM users WHERE email = :email"),
    {"email": email},
)
```

```python
user = session.query(User).filter(User.email == email).one_or_none()
```

#### Verification Steps

- [ ] All database access uses SQLAlchemy or bound parameters
- [ ] No raw SQL string interpolation
- [ ] Dynamic sorting/filtering is allowlisted
- [ ] Migration backfills also avoid string interpolation

### 4. Authentication and Authorization

#### Require Auth Explicitly

```python
from fastapi import Depends, HTTPException, status


def require_admin(current_user=Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return current_user
```

```python
@router.delete("/users/{user_id}")
async def delete_user(user_id: str, _: User = Depends(require_admin)):
    return await user_service.delete_user(user_id)
```

#### Frontend Guarding

- Protected routes live under `frontend/app/`; auth is the `/login` route + `frontend/lib/auth.ts`
- Frontend checks do not replace backend authorization checks
- Never grant admin from client code; Firebase custom claims must be set
  server-side only

#### Verification Steps

- [ ] Sensitive endpoints require auth dependencies
- [ ] Admin actions verify admin claims on the backend
- [ ] Ownership checks happen server-side before mutations
- [ ] Dev bypass logic is gated to non-production environments only
- [ ] Frontend auth guards mirror but do not replace backend checks

### 5. XSS Prevention

#### Default to Escaped Rendering

- Prefer normal React rendering over raw HTML
- Treat `dangerouslySetInnerHTML` as exceptional and sanitize first

```tsx
import DOMPurify from "isomorphic-dompurify";

export function SanitizedHtml({ html }: { html: string }) {
  const clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  return <div dangerouslySetInnerHTML={{ __html: clean }} />;
}
```

#### Verification Steps

- [ ] No unsafe HTML rendering without sanitization
- [ ] Dynamic URLs are validated before rendering or redirecting
- [ ] Error content returned by backend is rendered safely
- [ ] Frontend components rely on React escaping by default

### 6. CSRF and Session Safety

For cookie-based authenticated mutations:

- use `HttpOnly`, `Secure`, and an explicit `SameSite` policy
- protect state-changing endpoints with CSRF controls where applicable
- avoid mixing cookie auth with unprotected cross-site mutation routes

#### Verification Steps

- [ ] Cookie settings are explicit and production-safe
- [ ] State-changing routes have CSRF protection when using cookies
- [ ] Session or refresh tokens are not stored in `localStorage`

### 7. Rate Limiting and Abuse Controls

Expensive or high-risk routes should not be unbounded.

```python
from slowapi import Limiter
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address)


@router.post("/images/generate")
@limiter.limit("10/minute")
async def generate_image(...):
    ...
```

#### Verification Steps

- [ ] Public and expensive endpoints have rate limits
- [ ] Auth endpoints are protected against brute force
- [ ] User-scoped limits exist where IP-only limiting is weak
- [ ] Webhooks and background entry points have abuse controls

### 8. Sensitive Data Exposure

#### Logging

```python
logger.info("User login", extra={"user_id": user.id, "email": user.email})
logger.info("Generation created", extra={"assertion_id": assertion.id, "user_id": user.id})
```

Do not log:

- passwords
- bearer tokens
- raw authorization headers
- secret env values
- full payment details
- full third-party API payloads if they contain private data

#### Error Handling

```python
from fastapi import HTTPException, status


try:
    result = service.run()
except ExternalServiceError:
    logger.exception("Image provider failed")
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Upstream service failed",
    )
```

#### Verification Steps

- [ ] Logs exclude secrets and private payloads
- [ ] User-facing errors are generic enough for safety
- [ ] Detailed context is kept server-side only
- [ ] Stack traces are not returned in API responses

### 9. Dependency and Supply-Chain Security

Use the package managers already in the repo:

```bash
cd frontend && npm audit
cd backend && pip-audit
```

#### Verification Steps

- [ ] No known critical vulnerabilities in direct dependencies
- [ ] Lockfiles are committed where expected
- [ ] New libraries are justified and maintained
- [ ] Dependency scope is minimal for the feature

### 10. CORS, Headers, and External Integrations

Review these carefully on any new external API work:

- CORS origins should be explicit, not wildcarded with credentials
- Webhook endpoints must verify signatures
- Redirect URLs and callback URLs must be allowlisted
- Background jobs should not trust provider responses blindly

#### Verification Steps

- [ ] CORS is narrowed to expected origins
- [ ] Security headers are configured where relevant
- [ ] Webhooks verify signatures/timestamps
- [ ] External API model names and params were verified against docs

## Security Tests

Add focused tests when changing security-sensitive code.

```python
def test_requires_auth(client):
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 401


def test_requires_admin(client, user_token_headers):
    response = client.delete("/api/v1/users/123", headers=user_token_headers)
    assert response.status_code == 403


def test_rejects_invalid_input(client, admin_token_headers):
    response = client.post(
        "/api/v1/users",
        json={"email": "not-an-email"},
        headers=admin_token_headers,
    )
    assert response.status_code == 422
```

For frontend security-sensitive flows, verify:

- auth-gated pages stay protected after refresh
- frontend API calls do not leak tokens into logs or URLs
- dangerous HTML is not rendered unsanitized

## Pre-Deployment Security Checklist

Before any production deployment:

- [ ] **Secrets**: No hardcoded secrets; env and secret-manager usage verified
- [ ] **Input Validation**: All new inputs validated at boundaries
- [ ] **SQL Safety**: Queries and backfills parameterized
- [ ] **XSS**: User content sanitized or safely escaped
- [ ] **CSRF/Session**: Cookie/session handling is explicit and safe
- [ ] **Authentication**: Proper auth dependency checks in backend
- [ ] **Authorization**: Admin and ownership checks verified server-side
- [ ] **Rate Limiting**: Added to exposed or expensive endpoints
- [ ] **CORS**: Explicit origin policy, especially if credentials are used
- [ ] **Error Handling**: No sensitive data in API responses
- [ ] **Logging**: No secrets or private payloads logged
- [ ] **Dependencies**: Relevant audits completed
- [ ] **Frontend Guards**: `AuthWrapper` used where route auth gating matters
- [ ] **External Integrations**: Provider params and signatures verified
- [ ] **Uploads/Webhooks**: Size/type/signature validation in place

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [Starlette Middleware](https://www.starlette.io/middleware/)
- [Web Security Academy](https://portswigger.net/web-security)

---

**Remember**: Security review is about actual attack paths and blast radius,
not just checklist theater. Focus on auth boundaries, untrusted input,
secrets, external integrations, and what a malicious user can do with the
new behavior.
