.DEFAULT_GOAL := help

VENV ?= .venv
SYSTEM_PYTHON ?= python3
PYTHON ?= $(VENV)/bin/python
COLLECTIONS_PATH ?= $(CURDIR)/.ansible/collections
TEST_ARGS ?= discover -s tests -v

export PATH := $(abspath $(dir $(PYTHON))):$(PATH)
export ANSIBLE_COLLECTIONS_PATH := $(COLLECTIONS_PATH)
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH := False

.PHONY: help setup check test syntax lint

help:
	@echo 'make setup   Create .venv and explicitly install pinned Python tools/collections'
	@echo 'make check   Run syntax, lint and offline tests; never install dependencies'
	@echo 'make syntax  Compile Python, parse shell scripts and syntax-check every playbook'
	@echo 'make lint    Check controller/test Python correctness and YAML duplicate keys'
	@echo 'make test    Run the offline unittest suite (native tool skips are reported)'
	@echo 'Use Python 3.12+; native fixtures use Docker Compose, OpenSSL, SOPS/age and Nginx.'
	@echo 'NGINX_BIN/COMPOSE_BIN select native parsers; DNSMASQ_BIN enables optional DNS tests.'
	@echo 'Browser checks and the Nginx parser are provisioned separately in CI.'
	@echo 'Overrides: VENV=/tmp/hou1-tools COLLECTIONS_PATH=/tmp/hou1-collections'
	@echo 'Focused check: make check TEST_ARGS="discover -s tests -p test_authentik_branding.py -v"'

setup:
	$(SYSTEM_PYTHON) -m venv "$(VENV)"
	"$(PYTHON)" -m pip install -r requirements-tooling.txt
	"$(VENV)/bin/ansible-galaxy" collection install --no-deps -r ansible/requirements.yml -p "$(COLLECTIONS_PATH)"

check: syntax lint test

syntax:
	"$(PYTHON)" scripts/validate.py

lint:
	"$(PYTHON)" -m ruff check scripts tests
	"$(PYTHON)" -m yamllint -c .yamllint.yml ansible .github/workflows .sops.yaml

test:
	"$(PYTHON)" scripts/validate.py test $(TEST_ARGS)
