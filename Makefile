.PHONY: help fmt fmt-check lint typecheck test test-all check serve serve-dev init-db snapshot migrate revision clean

UV := uv run

help:
	@echo "make fmt              - ruff format (writes)"
	@echo "make fmt-check        - ruff format --check"
	@echo "make lint             - ruff check"
	@echo "make typecheck        - pyright"
	@echo "make test             - pytest (skips 'integration' marker)"
	@echo "make test-all         - pytest including integration tests"
	@echo "make check            - fmt-check + lint + typecheck + test (the CI gate)"
	@echo "make serve            - uvicorn (canonical, no reload)"
	@echo "make serve-dev        - uvicorn --reload"
	@echo "make init-db          - create data/ and run alembic upgrade head"
	@echo "make snapshot         - back up data/apfun.db to data/backups/ (pre-migration)"
	@echo "make migrate          - snapshot, then alembic upgrade head"
	@echo "make revision MSG=... - alembic autogenerate a new revision"
	@echo "make clean            - remove __pycache__ and tool caches"

fmt:
	$(UV) ruff format apfun tests scripts migrations/env.py

fmt-check:
	$(UV) ruff format --check apfun tests scripts migrations/env.py

lint:
	$(UV) ruff check apfun tests scripts migrations/env.py

typecheck:
	$(UV) pyright

test:
	$(UV) pytest -q -m "not integration"

test-all:
	$(UV) pytest -q

check: fmt-check lint typecheck test

serve:
	$(UV) uvicorn apfun.main:app --host 0.0.0.0 --port 4000

serve-dev:
	$(UV) uvicorn apfun.main:app --host 0.0.0.0 --port 4000 --reload

init-db:
	$(UV) python scripts/init_db.py

snapshot:
	bash scripts/db_snapshot.sh

# Snapshots the DB first (pre-migration backup discipline, feedback 029).
# Prefer `make migrate` over raw `alembic upgrade head` so the snapshot
# always happens.
migrate: snapshot
	$(UV) alembic upgrade head

revision:
	@[ -n "$(MSG)" ] || (echo "Usage: make revision MSG='your description'" && exit 1)
	$(UV) alembic revision --autogenerate -m "$(MSG)"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .pyright .pytest_cache
