# Workout Log. Run `make help` for the list.

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
SHARED  := shared/exercise_catalog.json
COPIES  := backend/src/workoutlog/exercise_catalog.json web/exercise_catalog.json

.PHONY: help venv sync-shared test test-py test-js check-secrets lint-template build deploy smoke serve clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t22

venv: ## Create .venv and install backend + test dependencies
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r backend/requirements-dev.txt
	@echo "venv ready: $(VENV)"

sync-shared: ## Copy the shared catalog into the Lambda package and web/
	@for dest in $(COPIES); do cp $(SHARED) $$dest && echo "  $(SHARED) -> $$dest"; done

test: test-py test-js ## Run every test

test-py: ## Backend tests (pytest + moto)
	cd backend && ../$(PY) -m pytest -q

test-js: ## Frontend tests (node --test, no dependencies)
	@if [ -d web/tests ] && ls web/tests/*.test.js >/dev/null 2>&1; then \
		node --test web/tests/; \
	else \
		echo "  no frontend tests yet (phase 4)"; \
	fi

check-secrets: ## Fail if a key-shaped string is in the repo. Run before committing.
	./scripts/check-secrets.sh

lint-template: ## Check backend/template.yaml with cfn-lint and sam validate
	$(VENV)/bin/cfn-lint backend/template.yaml
	cd backend && sam validate --lint

build: lint-template ## sam build into backend/.aws-sam
	cd backend && sam build --cached

deploy: ## Test, build and deploy the backend stack (needs AWS sign-in, docs/04)
	./scripts/deploy-backend.sh

smoke: ## Check the deployed stack from outside, through CloudFront
	./scripts/smoke-test.sh

serve: ## Serve web/ at http://localhost:8000 for local UI work
	cd web && python3 -m http.server 8000

clean: ## Remove build and test artefacts
	rm -rf .aws-sam backend/.aws-sam backend/.pytest_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
