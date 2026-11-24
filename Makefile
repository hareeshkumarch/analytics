.PHONY: doctor dev stop migrate test lint lock build smoke backup-dev restore-dev

COMPOSE=docker compose

doctor:
	python3 scripts/local_runner/doctor.py

dev:
	$(COMPOSE) up -d --build

stop:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec api alembic upgrade head

test:
	cd backend && python3 -m pytest -v

lint:
	cd backend && ruff check app tests && mypy app

lock:
	cd backend && uv pip compile pyproject.toml --universal --generate-hashes -q -o requirements.lock
	cd backend && uv pip compile pyproject.toml --extra dev --universal --generate-hashes -q -o requirements-dev.lock
	cd frontend && npm install --package-lock-only

build:
	$(COMPOSE) build

smoke:
	curl -sf http://127.0.0.1:8080/api/v1/health | python3 -m json.tool

backup-dev:
	bash scripts/local_runner/backup_dev.sh

restore-dev:
	bash scripts/local_runner/restore_dev.sh $(BACKUP_DIR)
