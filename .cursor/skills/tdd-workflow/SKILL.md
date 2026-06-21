---
name: tdd-workflow
description: Use this skill when writing new features, fixing bugs, or refactoring code in this repo. Enforces test-driven development for the FastAPI backend and Next.js frontend, with unit, integration, and E2E coverage where appropriate.
---

# Test-Driven Development Workflow

This skill keeps development in this repo test-first and reviewable.

## When to Activate

- Writing new backend or frontend features
- Fixing bugs
- Refactoring existing code
- Adding API endpoints
- Creating or changing UI flows
- Changing service integrations or business rules

## Repo Testing Surface

- Backend unit and integration tests: `backend/` with `pytest`
- Frontend quality gates: `webapp/` with `pnpm run lint` and
  `pnpm run type-check`
- Frontend tests: use existing test framework if present; do not invent a
  second framework unnecessarily
- E2E tests: use Playwright for critical user flows when the repo already
  covers that path or the change is high-risk

## Core Principles

### 1. Tests Before Code

Write the failing test first whenever feasible, then implement the
minimum code to make it pass.

### 2. Coverage Matches Risk

- Unit tests for isolated logic
- Integration tests for API, DB, service, and auth behavior
- E2E only for critical user paths or regressions that unit tests cannot
  meaningfully prove

### 3. Verify the Real Behavior

Test observable behavior, API contracts, permissions, and failure modes,
not internal implementation details.

## TDD Workflow Steps

### Step 1: Define the User or System Behavior

Use a short behavior statement:

```text
As an admin, I can archive a CRM project so that old work no longer shows
in the active list.
```

Or for backend-only work:

```text
When an invalid image provider payload is received, the API returns 422 and
does not create a shoot record.
```

### Step 2: List the Test Cases

For each change, define:

- happy path
- validation failures
- auth or permission boundaries
- edge cases and fallback behavior
- regression cases tied to the bug being fixed

### Step 3: Write the Failing Test

#### Backend Example

```python
def test_create_user_rejects_invalid_email(client, admin_token_headers):
    response = client.post(
        "/api/v1/users",
        json={"email": "not-an-email", "name": "Test"},
        headers=admin_token_headers,
    )

    assert response.status_code == 422
```

#### Frontend Example

```tsx
it("shows validation feedback when the form is submitted empty", async () => {
  render(<CreateShootForm />);

  await userEvent.click(screen.getByRole("button", { name: /create shoot/i }));

  expect(screen.getByText(/name is required/i)).toBeInTheDocument();
});
```

### Step 4: Run the Test and Confirm It Fails

Use the smallest command that proves the failure.

```bash
cd backend && pytest tests/path/to/test_users.py -k invalid_email
```

```bash
cd webapp && pnpm run lint
cd webapp && pnpm run type-check
```

If a frontend test runner exists in the repo, run the targeted failing
test there too. Do not claim TDD without observing a failing check first.

### Step 5: Implement the Minimum Fix

Keep the implementation narrow:

- do not widen scope while the test is still red
- prefer the simplest change that satisfies the contract
- fix the root cause, not just the symptom

### Step 6: Run the Targeted Test Again

```bash
cd backend && pytest tests/path/to/test_users.py -k invalid_email
```

Then run adjacent tests affected by the change.

### Step 7: Refactor Without Breaking Green

Once tests pass:

- remove duplication
- improve naming
- move logic to the right layer
- tighten error handling
- keep behavior unchanged

### Step 8: Run the Full Relevant Verification Set

Backend-focused changes:

```bash
cd backend && pytest
```

Frontend-focused changes:

```bash
cd webapp && pnpm run lint
cd webapp && pnpm run type-check
```

Cross-stack or high-risk flows:

- run both backend and frontend checks
- add or run E2E coverage for critical paths when needed

## Test Patterns

### Backend Unit or Integration Test Pattern

```python
def test_admin_can_delete_user(client, admin_token_headers):
    response = client.delete(
        "/api/v1/users/123",
        headers=admin_token_headers,
    )

    assert response.status_code == 204


def test_non_admin_cannot_delete_user(client, user_token_headers):
    response = client.delete(
        "/api/v1/users/123",
        headers=user_token_headers,
    )

    assert response.status_code == 403
```

### Service Test Pattern

```python
def test_credit_cost_uses_expected_model_mapping():
    cost = get_credit_cost(model="gemini-2.5-pro", resolution="1024")
    assert cost == 20
```

### Frontend Behavior Test Pattern

```tsx
it("disables submit while request is in flight", async () => {
  render(<BulkShootForm />);

  await userEvent.type(screen.getByLabelText(/name/i), "Summer Drop");
  await userEvent.click(screen.getByRole("button", { name: /create bulk shoot/i }));

  expect(screen.getByRole("button", { name: /create bulk shoot/i })).toBeDisabled();
});
```

### Playwright Pattern for Critical Flows

```ts
test("admin can open CRM project details", async ({ page }) => {
  await page.goto("/crm/projects");
  await page.getByRole("link", { name: /project details/i }).click();
  await expect(page).toHaveURL(/\/crm\/projects\/.+/);
});
```

## Repo File Organization

Backend:

```text
backend/
├── app/
├── tests/
│   ├── api/
│   ├── services/
│   └── ...
```

Frontend:

```text
webapp/
├── app/
├── components/
├── lib/
└── tests/    # if present in this repo's frontend setup
```

E2E:

```text
e2e/ or webapp/e2e/   # follow the repo's existing Playwright layout
```

## Mocking Guidance

Mock external boundaries, not your own business logic.

Good candidates to mock:

- third-party API clients
- slow provider calls
- billing or credit providers
- network failures
- time-sensitive external dependencies

Prefer real validation and real serialization in tests unless the test is
specifically about failure injection.

## Common Mistakes to Avoid

### ❌ Wrong: Testing Implementation Details

```python
assert service._cache_key == "user:123"
```

### ✅ Correct: Test the Observable Result

```python
assert response.json()["data"]["id"] == 123
```

### ❌ Wrong: Writing Code Before Proving the Failure

- implement feature
- then add a test afterward

### ✅ Correct: Capture the Failure First

- write or update the test
- run it and confirm red
- implement the minimum fix
- rerun and confirm green

### ❌ Wrong: Frontend-Only Fix for Backend Authorization

- hide the button
- skip backend permission enforcement

### ✅ Correct: Test Both Sides

- frontend guard for UX
- backend permission test for actual security

### ❌ Wrong: Forgetting Bulk Flow Parity

- fix the single shoot flow only

### ✅ Correct: Check Related Flow

- if generation UX changes, review `shoot` and `bulk shoot` paths together

## Continuous Testing

During development, run small targeted checks first, then widen:

```bash
cd backend && pytest tests/path/to/test_file.py
cd webapp && pnpm run lint
cd webapp && pnpm run type-check
```

For migration work, also validate with the repo migration script:

```bash
backend/scripts/test_alembic_migrations.sh
```

For external API integration changes:

- run the dedicated service smoke/integration test in
  `backend/scripts/service_tests/` when available
- if none exists, add one before claiming completion

## Best Practices

1. Write the smallest failing test that proves the bug or requirement.
2. Use descriptive test names that explain the behavior.
3. Keep Arrange-Act-Assert structure obvious.
4. Test auth, permissions, and validation on new endpoints.
5. Prefer deterministic tests over time-based waits.
6. Use semantic selectors in UI and E2E tests.
7. Keep tests isolated; each test should set up its own data.
8. Do not weaken assertions just to make the suite green.
9. Run the narrowest useful command first, then full validation.
10. Report the exact commands and results when closing the task.

## Success Metrics

- the bug or feature is captured by tests
- the first relevant test was observed failing before the fix
- targeted tests pass after the implementation
- full relevant checks pass for the changed area
- coverage and verification are appropriate to the risk of the change

---

**Remember**: TDD in this repo is not about maximizing test count. It is
about proving behavior, protecting high-risk paths, and making changes
safe to ship.
