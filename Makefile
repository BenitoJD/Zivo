.PHONY: dev setup doctor migrate

dev:
	./scripts/dev.sh start

setup:
	./scripts/dev.sh setup

doctor:
	./scripts/dev.sh doctor

schema:
	./scripts/dev.sh db schema
