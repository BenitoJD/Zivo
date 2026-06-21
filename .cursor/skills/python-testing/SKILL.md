---
name: python-testing
description: Python pytest patterns for this repo's FastAPI backend and service layer. Use for fixtures, parametrization, assertions, mocking, async tests, and organizing backend tests.
---

# Python Testing Patterns

Pytest-focused patterns for backend tests in this repo.

Use `testing-strategy` first when deciding what kind of test to write. Use this skill for the Python/pytest mechanics once the test type is clear.

## Repo Focus

In this repo, use this skill mainly for:

- FastAPI endpoint tests in `backend/tests/`
- service-layer tests for `backend/app/services/`
- scripts and utility tests in `backend/`
- auth and permission tests for admin/user flows

Pair with:

- `testing-strategy` for test selection and mocking boundaries
- `security-review` when testing auth, uploads, secrets, or webhook logic
- `alembic-migrations` for migration validation

## When to Activate

- Writing or updating backend tests with pytest
- Designing backend pytest suites
- Reviewing Python test coverage
- Setting up pytest fixtures or async test patterns

Do not default to broad coverage work if the changed area only needs a
small targeted regression or endpoint test. Match test scope to risk.

## Core Pytest Guidance

### Coverage Requirements

- **Target**: 80%+ code coverage
- **Critical paths**: 100% coverage required
- Use `pytest --cov` to measure coverage

```bash
pytest --cov=mypackage --cov-report=term-missing --cov-report=html
```

## pytest Fundamentals

### Basic Test Structure

```python
import pytest

def test_addition():
    """Test basic addition."""
    assert 2 + 2 == 4

def test_string_uppercase():
    """Test string uppercasing."""
    text = "hello"
    assert text.upper() == "HELLO"

def test_list_append():
    """Test list append."""
    items = [1, 2, 3]
    items.append(4)
    assert 4 in items
    assert len(items) == 4
```

### Assertions

```python
# Equality
assert result == expected

# Inequality
assert result != unexpected

# Truthiness
assert result  # Truthy
assert not result  # Falsy
assert result is True  # Exactly True
assert result is False  # Exactly False
assert result is None  # Exactly None

# Membership
assert item in collection
assert item not in collection

# Comparisons
assert result > 0
assert 0 <= result <= 100

# Type checking
assert isinstance(result, str)

# Exception testing (preferred approach)
with pytest.raises(ValueError):
    raise ValueError("error message")

# Check exception message
with pytest.raises(ValueError, match="invalid input"):
    raise ValueError("invalid input provided")

# Check exception attributes
with pytest.raises(ValueError) as exc_info:
    raise ValueError("error message")
assert str(exc_info.value) == "error message"
```

## Fixtures

### Basic Fixture Usage

```python
import pytest

@pytest.fixture
def sample_data():
    """Fixture providing sample data."""
    return {"name": "Alice", "age": 30}

def test_sample_data(sample_data):
    """Test using the fixture."""
    assert sample_data["name"] == "Alice"
    assert sample_data["age"] == 30
```

### Fixture with Setup/Teardown

```python
@pytest.fixture
def database():
    """Fixture with setup and teardown."""
    # Setup
    db = build_test_db()
    seed_test_data(db)

    yield db  # Provide to test

    # Teardown
    db.close()

def test_database_query(database):
    """Test database operations."""
    result = database.query("SELECT * FROM users")
    assert len(result) > 0
```

### Fixture Scopes

```python
# Function scope (default) - runs for each test
@pytest.fixture
def temp_file():
    with open("temp.txt", "w") as f:
        yield f
    os.remove("temp.txt")

# Module scope - runs once per module
@pytest.fixture(scope="module")
def module_db():
    db = Database(":memory:")
    db.create_tables()
    yield db
    db.close()

# Session scope - runs once per test session
@pytest.fixture(scope="session")
def shared_resource():
    resource = ExpensiveResource()
    yield resource
    resource.cleanup()
```

### Fixture with Parameters

```python
@pytest.fixture(params=[1, 2, 3])
def number(request):
    """Parameterized fixture."""
    return request.param

def test_numbers(number):
    """Test runs 3 times, once for each parameter."""
    assert number > 0
```

### Using Multiple Fixtures

```python
@pytest.fixture
def user():
    return User(id=1, name="Alice")

@pytest.fixture
def admin():
    return User(id=2, name="Admin", role="admin")

def test_user_admin_interaction(user, admin):
    """Test using multiple fixtures."""
    assert admin.can_manage(user)
```

### Autouse Fixtures

```python
@pytest.fixture(autouse=True)
def reset_config():
    """Automatically runs before every test."""
    Config.reset()
    yield
    Config.cleanup()

def test_without_fixture_call():
    # reset_config runs automatically
    assert Config.get_setting("debug") is False
```

### Conftest.py for Shared Fixtures

```python
# tests/conftest.py
import pytest

@pytest.fixture
def client():
    """Shared fixture for all tests."""
    app = build_test_app()
    with TestClient(app) as client:
        yield client

@pytest.fixture
def admin_token_headers():
    """Headers for admin-only endpoint tests."""
    return {
        "X-Dev-Auth-Uid": "sample-admin-1",
        "X-Dev-Auth-Admin": "true",
    }
```

For this repo, prefer fixtures that make role behavior explicit:

- `user_token_headers`
- `admin_token_headers`
- seeded entities tied to the exact endpoint or service under test

## Parametrization

### Basic Parametrization

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("PyThOn", "PYTHON"),
])
def test_uppercase(input, expected):
    """Test runs 3 times with different inputs."""
    assert input.upper() == expected
```

### Multiple Parameters

```python
@pytest.mark.parametrize("a,b,expected", [
    (2, 3, 5),
    (0, 0, 0),
    (-1, 1, 0),
    (100, 200, 300),
])
def test_add(a, b, expected):
    """Test addition with multiple inputs."""
    assert add(a, b) == expected
```

### Parametrize with IDs

```python
@pytest.mark.parametrize("input,expected", [
    ("valid@email.com", True),
    ("invalid", False),
    ("@no-domain.com", False),
], ids=["valid-email", "missing-at", "missing-domain"])
def test_email_validation(input, expected):
    """Test email validation with readable test IDs."""
    assert is_valid_email(input) is expected
```

### Parametrized Fixtures

```python
@pytest.fixture(params=["sqlite", "postgresql"])
def db(request):
    """Test against multiple database backends."""
    if request.param == "sqlite":
        return build_sqlite_test_db()
    elif request.param == "postgresql":
        return build_postgres_test_db()

def test_database_operations(db):
    """Test runs 3 times, once for each database."""
    result = db.query("SELECT 1")
    assert result is not None
```

## Markers and Test Selection

### Custom Markers

```python
# Mark slow tests
@pytest.mark.slow
def test_slow_operation():
    time.sleep(5)

# Mark integration tests
@pytest.mark.integration
def test_api_integration():
    response = requests.get("https://api.example.com")
    assert response.status_code == 200

# Mark unit tests
@pytest.mark.unit
def test_unit_logic():
    assert calculate(2, 3) == 5
```

### Run Specific Tests

```bash
# Run only fast tests
pytest -m "not slow"

# Run only integration tests
pytest -m integration

# Run integration or slow tests
pytest -m "integration or slow"

# Run tests marked as unit but not slow
pytest -m "unit and not slow"
```

### Configure Markers in pytest.ini

```ini
[pytest]
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
    api: marks tests as requiring the FastAPI app or HTTP layer
```

## Mocking and Patching

### Mocking Functions

```python
from unittest.mock import patch, Mock

@patch("mypackage.external_api_call")
def test_with_mock(api_call_mock):
    """Test with mocked external API."""
    api_call_mock.return_value = {"status": "success"}

    result = my_function()

    api_call_mock.assert_called_once()
    assert result["status"] == "success"
```

### Mocking Return Values

```python
@patch("mypackage.Database.connect")
def test_database_connection(connect_mock):
    """Test with mocked database connection."""
    connect_mock.return_value = MockConnection()

    db = build_test_db()
    db.connect()

    connect_mock.assert_called_once_with("localhost")
```

### Mocking Exceptions

```python
@patch("mypackage.api_call")
def test_api_error_handling(api_call_mock):
    """Test error handling with mocked exception."""
    api_call_mock.side_effect = ConnectionError("Network error")

    with pytest.raises(ConnectionError):
        api_call()

    api_call_mock.assert_called_once()
```

### Mocking Context Managers

```python
@patch("builtins.open", new_callable=mock_open)
def test_file_reading(mock_file):
    """Test file reading with mocked open."""
    mock_file.return_value.read.return_value = "file content"

    result = read_file("test.txt")

    mock_file.assert_called_once_with("test.txt", "r")
    assert result == "file content"
```

### Using Autospec

```python
@patch("mypackage.DBConnection", autospec=True)
def test_autospec(db_mock):
    """Test with autospec to catch API misuse."""
    db = db_mock.return_value
    db.query("SELECT * FROM users")

    # This would fail if DBConnection doesn't have query method
    db_mock.assert_called_once()
```

### Mock Class Instances

```python
class TestUserService:
    @patch("mypackage.UserRepository")
    def test_create_user(self, repo_mock):
        """Test user creation with mocked repository."""
        repo_mock.return_value.save.return_value = User(id=1, name="Alice")

        service = UserService(repo_mock.return_value)
        user = service.create_user(name="Alice")

        assert user.name == "Alice"
        repo_mock.return_value.save.assert_called_once()
```

### Mock Property

```python
@pytest.fixture
def mock_config():
    """Create a mock with a property."""
    config = Mock()
    type(config).debug = PropertyMock(return_value=True)
    type(config).api_key = PropertyMock(return_value="test-key")
    return config

def test_with_mock_config(mock_config):
    """Test with mocked config properties."""
    assert mock_config.debug is True
    assert mock_config.api_key == "test-key"
```

## Testing Async Code

### Async Tests with pytest-asyncio

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await async_add(2, 3)
    assert result == 5

@pytest.mark.asyncio
async def test_async_with_fixture(async_client):
    """Test async with async fixture."""
    response = await async_client.get("/api/users")
    assert response.status_code == 200
```

### Async Fixture

```python
@pytest.fixture
async def async_client():
    """Async fixture providing async test client."""
    app = build_test_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_api_endpoint(async_client):
    """Test using async fixture."""
    response = await async_client.get("/api/data")
    assert response.status_code == 200
```

### Mocking Async Functions

```python
@pytest.mark.asyncio
@patch("mypackage.async_api_call")
async def test_async_mock(api_call_mock):
    """Test async function with mock."""
    api_call_mock.return_value = {"status": "ok"}

    result = await my_async_function()

    api_call_mock.assert_awaited_once()
    assert result["status"] == "ok"
```

## Testing Exceptions

### Testing Expected Exceptions

```python
def test_divide_by_zero():
    """Test that dividing by zero raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)

def test_custom_exception():
    """Test custom exception with message."""
    with pytest.raises(ValueError, match="invalid input"):
        validate_input("invalid")
```

### Testing Exception Attributes

```python
def test_exception_with_details():
    """Test exception with custom attributes."""
    with pytest.raises(CustomError) as exc_info:
        raise CustomError("error", code=400)

    assert exc_info.value.code == 400
    assert "error" in str(exc_info.value)
```

## Testing Side Effects

### Testing File Operations

```python
import tempfile
import os

def test_file_processing():
    """Test file processing with temp file."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("test content")
        temp_path = f.name

    try:
        result = process_file(temp_path)
        assert result == "processed: test content"
    finally:
        os.unlink(temp_path)
```

### Testing with pytest's tmp_path Fixture

```python
def test_with_tmp_path(tmp_path):
    """Test using pytest's built-in temp path fixture."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = process_file(str(test_file))
    assert result == "hello world"
    # tmp_path automatically cleaned up
```

### Testing with tmpdir Fixture

```python
def test_with_tmpdir(tmpdir):
    """Test using pytest's tmpdir fixture."""
    test_file = tmpdir.join("test.txt")
    test_file.write("data")

    result = process_file(str(test_file))
    assert result == "data"
```

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                 # Shared fixtures
├── __init__.py
├── unit/                       # Unit tests
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_utils.py
│   └── test_services.py
├── integration/                # Integration tests
│   ├── __init__.py
│   ├── test_api.py
│   └── test_database.py
└── e2e/                        # End-to-end tests
    ├── __init__.py
    └── test_user_flow.py
```

### Test Classes

```python
class TestUserService:
    """Group related tests in a class."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup runs before each test in this class."""
        self.service = UserService()

    def test_create_user(self):
        """Test user creation."""
        user = self.service.create_user("Alice")
        assert user.name == "Alice"

    def test_delete_user(self):
        """Test user deletion."""
        user = User(id=1, name="Bob")
        self.service.delete_user(user)
        assert not self.service.user_exists(1)
```

## Best Practices

### DO

- **Follow TDD**: Write tests before code (red-green-refactor)
- **Test one thing**: Each test should verify a single behavior
- **Use descriptive names**: `test_user_login_with_invalid_credentials_fails`
- **Use fixtures**: Eliminate duplication with fixtures
- **Mock external dependencies**: Don't depend on external services
- **Test edge cases**: Empty inputs, None values, boundary conditions
- **Aim for 80%+ coverage**: Focus on critical paths
- **Keep tests fast**: Use marks to separate slow tests

### DON'T

- **Don't test implementation**: Test behavior, not internals
- **Don't use complex conditionals in tests**: Keep tests simple
- **Don't ignore test failures**: All tests must pass
- **Don't test third-party code**: Trust libraries to work
- **Don't share state between tests**: Tests should be independent
- **Don't catch exceptions in tests**: Use `pytest.raises`
- **Don't use print statements**: Use assertions and pytest output
- **Don't write tests that are too brittle**: Avoid over-specific mocks

## Common Patterns

### Testing API Endpoints (FastAPI)

```python
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


def test_get_user(client):
    response = client.get("/api/users/1")
    assert response.status_code == 200
    assert response.json()["id"] == 1


def test_create_user(client):
    response = client.post(
        "/api/users",
        json={"name": "Alice", "email": "alice@example.com"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Alice"
```

### Testing Database Operations

```python
@pytest.fixture
def db_session():
    """Create a test database session."""
    session = Session(bind=engine)
    session.begin_nested()
    yield session
    session.rollback()
    session.close()

def test_create_user(db_session):
    user = User(name="Alice", email="alice@example.com")
    db_session.add(user)
    db_session.commit()

    retrieved = db_session.query(User).filter_by(name="Alice").first()
    assert retrieved.email == "alice@example.com"
```

### Testing Class Methods

```python
class TestCalculator:
    @pytest.fixture
    def calculator(self):
        return Calculator()

    def test_add(self, calculator):
        assert calculator.add(2, 3) == 5

    def test_divide_by_zero(self, calculator):
        with pytest.raises(ZeroDivisionError):
            calculator.divide(10, 0)
```

## pytest Configuration

### pytest.ini

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    --strict-markers
    --disable-warnings
    --cov=mypackage
    --cov-report=term-missing
    --cov-report=html
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

### pyproject.toml

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--cov=mypackage",
    "--cov-report=term-missing",
    "--cov-report=html",
]
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests",
    "unit: marks tests as unit tests",
]
```

## Running Tests

```bash
# Run all tests
pytest

# Run specific file
pytest tests/test_utils.py

# Run specific test
pytest tests/test_utils.py::test_function

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=mypackage --cov-report=html

# Run only fast tests
pytest -m "not slow"

# Run until first failure
pytest -x

# Run and stop on N failures
pytest --maxfail=3

# Run last failed tests
pytest --lf

# Run tests with pattern
pytest -k "test_user"

# Run with debugger on failure
pytest --pdb
```

## Quick Reference

| Pattern | Usage |
|---------|-------|
| `pytest.raises()` | Test expected exceptions |
| `@pytest.fixture()` | Create reusable test fixtures |
| `@pytest.mark.parametrize()` | Run tests with multiple inputs |
| `@pytest.mark.slow` | Mark slow tests |
| `pytest -m "not slow"` | Skip slow tests |
| `@patch()` | Mock functions and classes |
| `tmp_path` fixture | Automatic temp directory |
| `pytest --cov` | Generate coverage report |
| `assert` | Simple and readable assertions |

**Remember**: Tests are code too. Keep them clean, readable, and maintainable. Good tests catch bugs; great tests prevent them.
