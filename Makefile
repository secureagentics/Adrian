# Adrian OSS — top-level developer Makefile
#
# Local-dev convention: every Python entry point goes through the
# project-local ``.venv`` so the bundled ``sdk/python/`` install (``pip
# install -e ./sdk/python``) cannot collide with a system-wide
# ``adrian-sdk`` wheel from PyPI. ``make sdk-install`` creates the venv
# and the editable install in one shot.

.PHONY: sdk-install sdk-test sdk-clean help

UV := $(shell command -v uv 2> /dev/null)

sdk-install: ## Create .venv and editable-install the bundled SDK
ifndef UV
	@echo "uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/"
	@exit 1
endif
	uv venv --allow-existing
	uv pip install -e ./sdk/python[dev]
	@echo ""
	@echo "Adrian SDK installed in .venv. Activate with:"
	@echo "  source .venv/bin/activate"
	@echo ""
	@echo "Or run anything via uv: 'uv run python ...'"

sdk-test: ## Run the bundled SDK test suite
	uv run --project ./sdk/python pytest sdk/python/tests

sdk-clean: ## Remove .venv and SDK build artefacts
	rm -rf .venv sdk/python/.venv sdk/python/dist sdk/python/build sdk/python/*.egg-info

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
