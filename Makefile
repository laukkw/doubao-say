PYTHON ?= .venv/bin/python

.PHONY: check test coverage lint compile shell whitespace release marketplace-check
check: lint test compile shell whitespace

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

coverage:
	PYTHONPATH=src $(PYTHON) -m coverage run -m unittest discover -s tests
	$(PYTHON) -m coverage report
	$(PYTHON) -m coverage xml

lint:
	$(PYTHON) -m ruff check src tests packaging

compile:
	$(PYTHON) -m compileall -q src/doubao_input tests packaging

shell:
	bash -n install.sh setup-omarchy.sh start.sh

whitespace:
	git diff --check

release: check
	$(PYTHON) packaging/build-release.py

marketplace-check:
	$(PYTHON) packaging/check-marketplace.py --secrets
