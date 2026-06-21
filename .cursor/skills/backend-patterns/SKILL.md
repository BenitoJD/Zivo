---
name: backend-patterns
description: >-
  Backend architecture patterns, API design, database optimization, and
  server-side best practices for FastAPI services.
---

# Backend Development Patterns

Backend architecture patterns and best practices for scalable FastAPI
applications.

## When to Activate

- Designing REST API endpoints
- Implementing router, service, and repository layers
- Optimizing database queries, indexes, and session usage
- Adding caching or background processing
- Structuring error handling and validation
- Building auth, logging, and rate-limiting dependencies

Use `fastapi` when the task is specifically about framework-level
FastAPI behavior, such as `Annotated`, dependency declarations,
response-model behavior, streaming responses, or other official FastAPI
conventions.

## FastAPI Essentials

- Load secrets and runtime configuration through Pydantic settings, not
  hardcoded constants.
- Keep large apps split by `APIRouter`; do not collapse all endpoints
  into one file.
- Use type hints and Pydantic models consistently for request and
  response contracts.
- Always set `response_model` so internal fields are not leaked by
  accident.
- Extract reusable request logic with `Depends(...)`, especially auth,
  DB session, pagination, and permission checks.
- Use `yield` dependencies when teardown is required, such as closing a
  DB session.
- Prefer async-compatible drivers and SQLAlchemy patterns when the code
  path is truly async.
- With SQLAlchemy relationships, use eager loading where needed instead
  of relying on implicit lazy loads in async code.
- Raise `HTTPException` with semantic status codes instead of embedding
  failure flags in 200 responses.
- Hash passwords; never store plaintext credentials.
- If cookie auth is used, configure CORS narrowly and add CSRF
  protection.
- For backend tests, prefer `pytest` with dependency overrides and use
  `httpx`/async test patterns when the endpoint path is async.
- Use `def` for blocking CPU-heavy work and `async def` for actual
  asynchronous I/O.
- Move expensive long-running work to a queue or worker and return `202
  Accepted` when the request should not block.
- Use structured logging and production-safe process management for
  deployed services.

## API Structure

### Resource-Oriented Routes

```text
GET    /api/markets
GET    /api/markets/{market_id}
POST   /api/markets
PUT    /api/markets/{market_id}
PATCH  /api/markets/{market_id}
DELETE /api/markets/{market_id}
```

### Router Pattern

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/{market_id}", response_model=MarketRead)
def get_market(
    market_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_auth),
):
    service = MarketService(MarketRepository(db))
    market = service.get_market(market_id)
    if market is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Market not found",
        )
    return market
```

## Layering

### Repository Pattern

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session


class MarketRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self, filters: MarketFilters) -> Sequence[Market]:
        query = select(Market)

        if filters.status is not None:
            query = query.where(Market.status == filters.status)

        if filters.limit is not None:
            query = query.limit(filters.limit)

        return self.db.execute(query).scalars().all()

    def find_by_id(self, market_id: str) -> Market | None:
        return self.db.get(Market, market_id)
```

### Service Layer

```python
class MarketService:
    def __init__(self, repo: MarketRepository):
        self.repo = repo

    def get_market(self, market_id: str) -> Market | None:
        return self.repo.find_by_id(market_id)

    def create_market(self, payload: CreateMarketRequest) -> Market:
        market = Market(**payload.model_dump())
        self.repo.db.add(market)
        self.repo.db.commit()
        self.repo.db.refresh(market)
        return market
```

## Validation

### Request and Response Models

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateMarketRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    status: str


class MarketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    created_at: datetime
```

## Error Handling

### Domain Errors and Exception Handlers

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


app = FastAPI()


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.message},
    )
```

### Prefer Explicit HTTP Errors

```python
from fastapi import HTTPException, status


def require_owner(user: CurrentUser, owner_id: str) -> None:
    if user.id != owner_id and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
```

## Authentication and Authorization

### Auth Dependency

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    return verify_token(credentials.credentials)
```

### Permission Dependency

```python
def require_admin(user: CurrentUser = Depends(require_auth)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
```

## Database Patterns

### Select Only What You Need

```python
query = (
    select(Market.id, Market.name, Market.status, Market.volume)
    .where(Market.status == "active")
    .order_by(Market.volume.desc())
    .limit(10)
)
rows = db.execute(query).all()
```

### Avoid N+1 Queries

```python
from sqlalchemy.orm import selectinload

markets = db.execute(
    select(Market).options(selectinload(Market.creator))
).scalars().all()
```

### Transaction Pattern

```python
def create_market_with_position(
    db: Session,
    market_payload: CreateMarketRequest,
    position_payload: CreatePositionRequest,
) -> Market:
    try:
        market = Market(**market_payload.model_dump())
        db.add(market)
        db.flush()

        position = Position(
            **position_payload.model_dump(),
            market_id=market.id,
        )
        db.add(position)
        db.commit()
        db.refresh(market)
        return market
    except Exception:
        db.rollback()
        raise
```

## Caching

### Cache-Aside

```python
async def get_market_cached(market_id: str, repo: MarketRepository) -> Market:
    cache_key = f"market:{market_id}"
    cached = await redis.get(cache_key)
    if cached:
        return MarketRead.model_validate_json(cached)

    market = repo.find_by_id(market_id)
    if market is None:
        raise ApiError(404, "Market not found")

    await redis.set(
        cache_key,
        MarketRead.model_validate(market).model_dump_json(),
        ex=300,
    )
    return market
```

## Rate Limiting

### Dependency-Based Limit

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


@router.get("")
@limiter.limit("100/minute")
def list_markets(request: Request, db: Session = Depends(get_db)):
    return MarketService(MarketRepository(db)).list_markets(MarketFilters())
```

## Background Jobs

### FastAPI BackgroundTasks

```python
from fastapi import BackgroundTasks


def index_market(market_id: str) -> None:
    ...


@router.post("/{market_id}/index")
def queue_market_index(
    market_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_admin),
):
    background_tasks.add_task(index_market, market_id)
    return {"success": True, "message": "Job queued"}
```

## Logging

### Structured Logging

```python
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


@router.get("")
def list_markets(db: Session = Depends(get_db)):
    request_id = str(uuid4())
    logger.info("Listing markets", extra={"request_id": request_id})
    return MarketService(MarketRepository(db)).list_markets(MarketFilters())
```

## Practical Rules

- Keep routers thin; business rules belong in services.
- Keep ORM session management in dependencies, not global state.
- Validate at the API boundary with Pydantic models.
- Prefer explicit exceptions over silent fallback behavior.
- Use background jobs for slow or retry-heavy work.
- Log request context, but never secrets or raw tokens.
- Add indexes before scaling read-heavy endpoints.

**Remember**: backend patterns should reduce operational risk and keep
the codebase easy to change. Prefer boring, explicit FastAPI structures
over clever abstractions.
