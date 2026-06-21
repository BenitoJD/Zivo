---
name: alembic-migrations
description: Create, rebase, and validate Alembic migrations in this repo. Use when generating migrations, fixing FK/enum issues, rebasing after git rebase, applying safe schema-change and rollout patterns, writing data backfills, and running upgrade/downgrade tests.
---

# Alembic Migrations

Safe, reviewable Alembic migration workflow for this repo's FastAPI + SQLAlchemy + PostgreSQL stack.

## When to Use

- Creating or altering tables in `backend/app/models/`
- Adding or removing columns, indexes, constraints, or enums
- Writing data backfills
- Reviewing migration safety before merge
- Rebasing branch migrations after git rebase
- Planning rollout of schema changes without breaking deployed code

## Workflow

### 1. Create migration

```bash
cd backend
alembic revision --autogenerate -m "short_message"
```

### 2. Review autogenerate output

Check the new file in `backend/alembic/versions/` for:

- unnamed foreign keys
- enum creation and cleanup
- destructive drops
- unexpected column type changes
- accidental defaults or nullable changes

### 3. Fix migration issues

Common fixes after autogenerate:

- **Foreign keys must be named** so downgrades can drop them.
  - Use explicit names in `op.create_foreign_key("fk_table_col_ref", ...)`
  - Drop by name in downgrade `op.drop_constraint("fk_table_col_ref", ...)`
- **Enums need manual cleanup** in downgrade.
  - Ensure `op.execute("DROP TYPE IF EXISTS enum_name")` runs after dropping dependent tables.

**Anti-patterns**

- If you create or revise a migration more than once in the **same PR**, treat it as a **first-time migration**: one clean `upgrade()` with final column names and `server_default`s — not a schema pass followed by `op.execute("UPDATE …")` patches.
- Do not "fix" your own migration in that PR by appending data backfills or CASE merges; rewrite the migration file or run `rebase_migration.sh` instead.
- Do not manually edit the production database instead of writing a migration.
- Do not mix a risky schema rewrite and a large data rewrite in one step.
- Do not assume Alembic autogenerate is correct without review.
- Do not edit a migration file that is already on `origin/main` (see **Merged vs branch migrations** below).

### 4. Test migrations

Use the repo helper:

```bash
backend/scripts/test_alembic_migrations.sh
```

It creates a unique temporary DB, runs `upgrade head`, `downgrade -5`, then `upgrade head` again, and drops the DB. Fix any failures (usually unnamed FKs or enum cleanup) and re-run until clean.

### 5. Rebase a migration after git rebase

Use the skill command:

```bash
<platform>/skills/alembic-migrations/scripts/rebase_migration.sh "short_message"
```

What it does:

1. Deletes branch migration files in `backend/alembic/versions` (files changed vs `origin/main` plus untracked migration files).
2. Drops and recreates the current DB from `DATABASE_URL`.
3. Runs `alembic upgrade head` to rebuild schema from existing migrations.
4. Regenerates migration via `alembic revision --autogenerate -m "short_message"`.
5. Verifies with `backend/scripts/test_alembic_migrations.sh`.

Optional:

```bash
<platform>/skills/alembic-migrations/scripts/rebase_migration.sh "short_message" --dry-run
```

Use `--dry-run` to see which files would be deleted without mutating anything.

## Core Principles

1. Every schema change must go through Alembic.
2. Never edit a migration whose file has reached `origin/main` (see below).
3. Separate schema changes from large data backfills when practical.
4. Prefer expand-contract over risky in-place renames or drops.
5. Validate upgrade and downgrade behavior before calling the work done.

### Merged vs branch migrations

In this repo, treat a migration as **merged** when its file is already on
`origin/main` (the PR that introduced it has been merged).

| State | Can you edit the migration file? |
|-------|----------------------------------|
| Migration exists only on a feature branch (not merged to `main`) | **Yes** — revise it in place or regenerate with `rebase_migration.sh` |
| Migration file is on `origin/main` | **No** — add a new migration instead |

> **Once a migration file reaches `origin/main`, treat it as immutable even if
> the production deploy hasn't run yet.** The cost of an extra migration is low;
> the cost of a broken revision chain is high.

How to check:

```bash
git fetch origin
git diff --name-only origin/main...HEAD -- backend/alembic/versions/
```

- Files listed in that diff are **branch-only** and still editable.
- Files that exist on `origin/main` but not in your branch diff are already
  merged; do not rewrite them.

If a migration is not merged yet, prefer one clean final `upgrade()` in the
same PR rather than stacking patch-style `UPDATE` steps (see anti-patterns
above).

## Safety Checklist

Before merging a migration:

- [ ] The migration matches the intended model diff
- [ ] Foreign keys are named explicitly
- [ ] New non-null columns have a safe rollout plan
- [ ] Enum creation/drop logic is explicit
- [ ] Backfills are bounded or batched if large
- [ ] Upgrade and downgrade paths were tested with the repo script

## Common Patterns

### Adding a Nullable Column

Safe first step when existing rows already exist.

```python
def upgrade():
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("users", "avatar_url")
```

### Adding a Non-Null Column Safely

Do not immediately add a required column to a populated table unless you also have a safe default or staged rollout.

Recommended sequence:

1. add the column as nullable
2. backfill existing rows
3. deploy app code that writes the field
4. make the column non-null in a later migration

### Naming Foreign Keys

Unnamed foreign keys create downgrade problems.

```python
op.create_foreign_key(
    "fk_tasks_user_id_users",
    "tasks",
    "users",
    ["user_id"],
    ["id"],
)

op.drop_constraint("fk_tasks_user_id_users", "tasks", type_="foreignkey")
```

### Enum Cleanup

If a migration creates a PostgreSQL enum, make sure downgrade cleans it up after dependent objects are removed.

```python
def downgrade():
    op.drop_table("image_jobs")
    op.execute("DROP TYPE IF EXISTS image_job_status")
```

### Expand-Contract Rename Pattern

Never rely on a direct rename when old and new app versions may overlap.

1. add the new column
2. backfill data
3. read/write both fields in application code if needed
4. stop using the old field
5. drop the old field in a later migration

### Large Backfills

Avoid one huge transaction for large tables.

```python
from alembic import op
import sqlalchemy as sa


def upgrade():
    connection = op.get_bind()
    batch_size = 5000

    while True:
        rows = connection.execute(
            sa.text(
                """
                SELECT id, username
                FROM users
                WHERE display_name IS NULL
                LIMIT :limit
                """
            ),
            {"limit": batch_size},
        ).fetchall()

        if not rows:
            break

        for row in rows:
            connection.execute(
                sa.text(
                    """
                    UPDATE users
                    SET display_name = :display_name
                    WHERE id = :id
                    """
                ),
                {"id": row.id, "display_name": row.username},
            )


def downgrade():
    pass
```

If the table is large enough to make migration runtime risky, prefer moving the backfill into an operational script or a controlled job.

## PostgreSQL Notes

### Index Creation

For existing large tables, think about write blocking before creating an index in the simplest possible way.

```sql
CREATE INDEX CONCURRENTLY idx_users_email ON users (email);
```

If using `CONCURRENTLY`, remember it cannot run inside a normal transaction block, so the migration may need special handling.

### Avoid Risky One-Step Changes

Examples to avoid without a staged plan:

- adding `NOT NULL` to a populated table in one step
- dropping a column still referenced by deployed code
- changing a column type with implicit lossy casts
- renaming a column when multiple app versions may run at once

## Review Questions

Ask these before approving a migration:

- What happens if old app code runs against the new schema?
- What happens if new app code runs before the migration?
- Is the downgrade actually safe and meaningful?
- Could this lock a hot table?
- Does this create or leave behind enum/type debt?

## Repo-Specific Notes

- Migration files live in `backend/alembic/versions/`
- Validation command is `backend/scripts/test_alembic_migrations.sh`
- **Merged** = migration file is on `origin/main`; **branch-only** = only on a feature branch
- When asked what migrations are new after a production deploy, diff
  `backend/alembic/versions/` against the last deployed `origin/main` ref,
  not just the current feature branch

**Remember**: migration quality is mostly about rollout safety, not just schema correctness. A migration is good only if it upgrades cleanly, downgrades predictably, and does not surprise deployed application code.
