.PHONY: dev setup doctor migrate seed test-migrations

dev:
	./scripts/dev.sh start

setup:
	./scripts/dev.sh setup

doctor:
	./scripts/dev.sh doctor

migrate:
	./scripts/dev.sh db migrate

schema: migrate

qb-schema: migrate

seed:
	./scripts/dev.sh db seed

test-migrations:
	backend/scripts/test_alembic_migrations.sh
