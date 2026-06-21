---
name: coding-standards
description: >-
  Universal coding standards, best practices, and patterns with a small
  repo-specific overlay for this FastAPI + Next.js codebase, including
  frontend and backend-friendly examples.
---

# Coding Standards & Best Practices

Universal coding standards applicable across all projects.

## Repo Notes

For this repo specifically:

- Backend is FastAPI in `backend/`
- Frontend is Next.js in `webapp/`
- Use `pnpm`, never `npm`
- Use `apiClient` for frontend API calls
- If you change the generation flow, check whether the bulk generation
  flow needs the same change
- Use `shoot` / `bulk shoot` terminology for generation work unless you
  explicitly mean the CRM project

Prefer the more specific skill when one exists:

- use `backend-patterns` for FastAPI service and router design
- use `frontend-patterns` for React and Next.js implementation details
- use `tdd-workflow` for change execution and test-first work
- use `security-review` for auth, secrets, uploads, and sensitive flows
- use `alembic-migrations` for actual Alembic changes

## When to Activate

- Starting a new project or module
- Reviewing code for quality and maintainability
- Refactoring existing code to follow conventions
- Enforcing naming, formatting, or structural consistency
- Setting up linting, formatting, or type-checking rules
- Onboarding new contributors to coding conventions

Do not use this skill as the main workflow for a concrete implementation
task when a more specific repo skill fits better. This skill should set
baseline standards, not replace repo execution workflows.

## Code Quality Principles

### 1. Readability First

- Code is read more than written
- Clear variable and function names
- Self-documenting code preferred over comments
- Consistent formatting

### 2. KISS (Keep It Simple, Stupid)

- Simplest solution that works
- Avoid over-engineering
- No premature optimization
- Easy to understand > clever code

### 3. DRY (Don't Repeat Yourself)

- Extract common logic into functions
- Create reusable components
- Share utilities across modules
- Avoid copy-paste programming

### 4. YAGNI (You Aren't Gonna Need It)

- Don't build features before they're needed
- Avoid speculative generality
- Add complexity only when required
- Start simple, refactor when needed

## Python/FastAPI Standards

### Validation at the Boundary

```python
from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=100)
```

- Validate incoming request data with Pydantic models.
- Do not pass raw request dictionaries deep into service code.
- Keep response models explicit when returning structured API data.

### Route Handler Structure

```python
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
    current_user: CurrentUser = Depends(require_auth),
):
    user = service.get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user
```

- Keep FastAPI route handlers thin.
- Put business logic in services, not in routers.
- Use dependencies for auth, DB session, and service construction.

### SQLAlchemy Query Discipline

```python
query = (
    select(User.id, User.email, User.created_at)
    .where(User.is_active.is_(True))
    .limit(20)
)
rows = db.execute(query).all()
```

- Select only the fields you need when full ORM objects are unnecessary.
- Avoid N+1 query patterns; use eager loading when relationships are needed.
- Use transactions explicitly for multi-step writes.

## TypeScript/JavaScript Standards

### Variable Naming

```typescript
// ✅ GOOD: Descriptive names
const shootSearchQuery = 'summer campaign'
const isUserAuthenticated = true
const totalRevenue = 1000

// ❌ BAD: Unclear names
const q = 'election'
const flag = true
const x = 1000
```

### Function Naming

```typescript
// ✅ GOOD: Verb-noun pattern
async function fetchShootData(shootId: string) { }
function calculateSimilarity(a: number[], b: number[]) { }
function isValidEmail(email: string): boolean { }

// ❌ BAD: Unclear or noun-only
async function load(id: string) { }
function similarity(a, b) { }
function email(e) { }
```

### Immutability Pattern (CRITICAL)

```typescript
// ✅ ALWAYS use spread operator
const updatedUser = {
  ...user,
  name: 'New Name'
}

const updatedArray = [...items, newItem]

// ❌ NEVER mutate directly
user.name = 'New Name'  // BAD
items.push(newItem)     // BAD
```

### Error Handling

```typescript
// ✅ GOOD: Comprehensive error handling
async function fetchData(url: string) {
  try {
    const response = await fetch(url)

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`)
    }

    return await response.json()
  } catch (error) {
    console.error('Fetch failed:', error)
    throw new Error('Failed to fetch data')
  }
}

// ❌ BAD: No error handling
async function fetchData(url) {
  const response = await fetch(url)
  return response.json()
}
```

### Async/Await Best Practices

```typescript
// ✅ GOOD: Parallel execution when possible
const [users, shoots, stats] = await Promise.all([
  fetchUsers(),
  fetchShoots(),
  fetchStats()
])

// ❌ BAD: Sequential when unnecessary
const users = await fetchUsers()
const shoots = await fetchShoots()
const stats = await fetchStats()
```

### Type Safety

```typescript
// ✅ GOOD: Proper types
interface ShootSummary {
  id: string
  name: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  created_at: string
}

function getShoot(id: string): Promise<ShootSummary> {
  // Implementation
}

// ❌ BAD: Using 'any'
function getShoot(id: any): Promise<any> {
  // Implementation
}
```

## React Best Practices

### Component Structure

```typescript
// ✅ GOOD: Functional component with types
interface ButtonProps {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary'
}

export function Button({
  children,
  onClick,
  disabled = false,
  variant = 'primary'
}: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`btn btn-${variant}`}
    >
      {children}
    </button>
  )
}

// ❌ BAD: No types, unclear structure
export function Button(props) {
  return <button onClick={props.onClick}>{props.children}</button>
}
```

### Custom Hooks

```typescript
// ✅ GOOD: Reusable custom hook
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value)
    }, delay)

    return () => clearTimeout(handler)
  }, [value, delay])

  return debouncedValue
}

// Usage
const debouncedQuery = useDebounce(searchQuery, 500)
```

### State Management

```typescript
// ✅ GOOD: Proper state updates
const [count, setCount] = useState(0)

// Functional update for state based on previous state
setCount(prev => prev + 1)

// ❌ BAD: Direct state reference
setCount(count + 1)  // Can be stale in async scenarios
```

### Conditional Rendering

```typescript
// ✅ GOOD: Clear conditional rendering
{isLoading && <Spinner />}
{error && <ErrorMessage error={error} />}
{data && <DataDisplay data={data} />}

// ❌ BAD: Ternary hell
{isLoading ? <Spinner /> : error ? <ErrorMessage error={error} /> : data ? <DataDisplay data={data} /> : null}
```

## API Design Standards

### REST API Conventions

```text
GET    /api/v1/shoots            # List shoots
GET    /api/v1/shoots/:id        # Get specific shoot
POST   /api/v1/shoots            # Create new shoot
PUT    /api/v1/shoots/:id        # Update shoot (full)
PATCH  /api/v1/shoots/:id        # Update shoot (partial)
DELETE /api/v1/shoots/:id        # Delete shoot

# Query parameters for filtering
GET /api/v1/shoots?status=completed&limit=10&offset=0
```

### Response Format

```python
# ✅ GOOD: Consistent response structure
return {
    "success": True,
    "data": shoots,
    "meta": {"total": 100, "page": 1, "limit": 10},
}

# Error response
raise HTTPException(status_code=400, detail="Invalid request")
```

### Input Validation

```python
from pydantic import BaseModel, Field


class CreateShootRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1, max_length=4000)
    product_ids: list[str] = Field(..., min_length=1)


@router.post("/api/v1/shoots", status_code=201)
def create_shoot(payload: CreateShootRequest):
    return {"success": True, "data": payload.model_dump()}
```

## File Organization

### Project Structure

```text
backend/
├── app/
│   ├── api/              # FastAPI routers
│   ├── services/         # Business logic
│   ├── models/           # SQLAlchemy models
│   └── schemas/          # Request/response models
└── tests/

webapp/
├── app/                  # Next.js App Router pages
├── components/           # React components
├── lib/                  # Client utilities, including apiClient
└── hooks/                # Custom hooks
```

### File Naming

```text
components/Button.tsx          # PascalCase for components
hooks/useAuth.ts              # camelCase with 'use' prefix
lib/formatDate.ts             # camelCase for utilities
types/shoot.types.ts          # camelCase with .types suffix
```

## Comments & Documentation

### When to Comment

```typescript
// ✅ GOOD: Explain WHY, not WHAT
// Use exponential backoff to avoid overwhelming the API during outages
const delay = Math.min(1000 * Math.pow(2, retryCount), 30000)

// Deliberately using mutation here for performance with large arrays
items.push(newItem)

// ❌ BAD: Stating the obvious
// Increment counter by 1
count++

// Set name to user's name
name = user.name
```

### JSDoc for Public APIs

```typescript
/**
 * Searches shoots using the repo's current search strategy.
 *
 * @param query - Natural language search query
 * @param limit - Maximum number of results (default: 10)
 * @returns Array of shoots sorted by relevance
 * @throws {Error} If the upstream service or cache layer fails
 *
 * @example
 * ```typescript
 * const results = await searchShoots('new summer campaign', 5)
 * console.log(results[0].name) // "Summer Campaign Shoot"
 * ```
 */
export async function searchShoots(
  query: string,
  limit: number = 10
): Promise<ShootSummary[]> {
  // Implementation
}
```

## Performance Best Practices

### Memoization

```typescript
import { useMemo, useCallback } from 'react'

// ✅ GOOD: Memoize expensive computations
const sortedShoots = useMemo(() => {
  return [...shoots].sort((a, b) => b.created_at.localeCompare(a.created_at))
}, [shoots])

// ✅ GOOD: Memoize callbacks
const handleSearch = useCallback((query: string) => {
  setSearchQuery(query)
}, [])
```

### Lazy Loading

```typescript
import { lazy, Suspense } from 'react'

// ✅ GOOD: Lazy load heavy components
const HeavyChart = lazy(() => import('./HeavyChart'))

export function Dashboard() {
  return (
    <Suspense fallback={<Spinner />}>
      <HeavyChart />
    </Suspense>
  )
}
```

### Database Queries

```python
# ✅ GOOD: Select only needed columns
query = select(Shoot.id, Shoot.name, Shoot.status).limit(10)
rows = db.execute(query).all()

# ❌ BAD: Select full rows when only summary data is needed
query = select(Shoot)
```

## Testing Standards

### Test Structure (AAA Pattern)

```typescript
test('calculates similarity correctly', () => {
  // Arrange
  const vector1 = [1, 0, 0]
  const vector2 = [0, 1, 0]

  // Act
  const similarity = calculateCosineSimilarity(vector1, vector2)

  // Assert
  expect(similarity).toBe(0)
})
```

### Test Naming

```typescript
// ✅ GOOD: Descriptive test names
test('returns empty array when no shoots match query', () => { })
test('throws error when required service credentials are missing', () => { })
test('returns validation error when required fields are missing', () => { })

// ❌ BAD: Vague test names
test('works', () => { })
test('test search', () => { })
```

## Code Smell Detection

Watch for these anti-patterns:

### 1. Long Functions

```typescript
// ❌ BAD: Function > 50 lines
function processShootData() {
  // 100 lines of code
}

// ✅ GOOD: Split into smaller functions
function processShootData() {
  const validated = validateData()
  const transformed = transformData(validated)
  return saveData(transformed)
}
```

### 2. Deep Nesting

```typescript
// ❌ BAD: 5+ levels of nesting
if (user) {
  if (user.isAdmin) {
    if (shoot) {
      if (shoot.isActive) {
        if (hasPermission) {
          // Do something
        }
      }
    }
  }
}

// ✅ GOOD: Early returns
if (!user) return
if (!user.isAdmin) return
if (!shoot) return
if (!shoot.isActive) return
if (!hasPermission) return

// Do something
```

### 3. Magic Numbers

```typescript
// ❌ BAD: Unexplained numbers
if (retryCount > 3) { }
setTimeout(callback, 500)

// ✅ GOOD: Named constants
const MAX_RETRIES = 3
const DEBOUNCE_DELAY_MS = 500

if (retryCount > MAX_RETRIES) { }
setTimeout(callback, DEBOUNCE_DELAY_MS)
```

**Remember**: Code quality is not negotiable. Clear, maintainable code
enables rapid development and confident refactoring.
