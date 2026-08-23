# Release recipe for jupyterlab-yukti.
#
# Split of duties:
#   checkpoint (~/.zshrc)  promotes branches            feature/* -> dev -> main
#   this file              releases one built version   build -> verify -> PyPI
#
# Four rules this file keeps, which `checkpoint --publish` does not:
#   1. It never changes git state on its own. It runs no `git add -A`.
#   2. Every step fails loud. No step hides an error to keep going.
#   3. It holds no branch policy. Release from whatever branch you choose.
#   4. It reads the version. Only `make bump` writes the version.

# Make keeps the blanks before an inline `#`, so every comment sits above its line.
DIST := jupyterlab-yukti
# Labextension directory. It must match the npm package name, not the PyPI name.
EXT := yukti
# A ~/.pypirc section, or `pypi`, or `testpypi`.
REPO ?= sizhky
# patch | minor | major
PART ?= patch
# Set to any value to skip the upload prompt.
YES ?=

VERSION := $(shell uv version --short 2>/dev/null)
NPMVER  := $(shell node -p "require('./package.json').version" 2>/dev/null)
TAG     := v$(VERSION)
RELENV  := .venv-release
# Scope every glob to this version, so a stale dist/ can never be uploaded.
WHL     := dist/*-$(VERSION)-*.whl
SDIST   := dist/*-$(VERSION).tar.gz
GATES   := guard build check verify
# Isolated, so the recipe ignores whatever the active environment holds.
TWINE := uvx twine

SHELL := /bin/bash
.NOTPARALLEL:
.PHONY: help bump clean guard build check verify tag publish push dry release

help:  ## show this help
	@printf '\n  %s %s  ->  %s\n\n' "$(DIST)" "$(VERSION)" "$(REPO)"
	@grep -hE '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /|/' \
		| awk -F'|' '{printf "  make %-9s %s\n", $$1, $$2}'
	@printf '\n  knobs: REPO=%s PART=%s YES=%s\n\n' "$(REPO)" "$(PART)" "$(YES)"

# --no-sync: write pyproject.toml and uv.lock, but leave the dev venv alone.
bump:  ## raise the version in pyproject.toml, uv.lock, and package.json
	uv version --no-sync --bump $(PART)
	npm version "$$(uv version --short)" --no-git-tag-version --allow-same-version
	@printf 'bumped to %s. commit, then run make release\n' "$$(uv version --short)"

clean:  ## delete every build output
	rm -rf dist build *.egg-info $(RELENV)
	npm run clean

guard:  ## refuse a release that cannot succeed
	@test -n "$(VERSION)" || { echo 'guard: cannot read the version'; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo 'guard: commit or stash first'; exit 1; }
	@test "$(VERSION)" = "$(NPMVER)" \
		|| { echo 'guard: pyproject $(VERSION) != package.json $(NPMVER); run make bump'; exit 1; }
	@! git rev-parse -q --verify refs/tags/$(TAG) >/dev/null \
		|| { echo 'guard: tag $(TAG) exists; run make bump'; exit 1; }
	@echo 'guard: ok'

build: clean  ## build the labextension, then the sdist and the wheel
	npm ci
	npm run build:prod
	uv build

check:  ## validate the package metadata
	$(TWINE) check --strict $(WHL) $(SDIST)

verify:  ## install the wheel in a throwaway env and prove it loads
	uv venv --clear -q $(RELENV)
	uv pip install -q --python $(RELENV)/bin/python "jupyterlab>=4,<5" $(WHL)
	$(RELENV)/bin/python -c 'import yukti; print("import ok:", yukti.__version__)'
	$(RELENV)/bin/jupyter labextension list 2>&1 | grep -E '$(EXT).*enabled.*OK'
	rm -rf $(RELENV)

tag:  ## create the annotated tag, locally only
	git tag -a $(TAG) -m "$(DIST) $(VERSION)"

publish:  ## upload to REPO -- PyPI never reuses a version, so this is final
	@ls $(WHL) $(SDIST) >/dev/null 2>&1 \
		|| { echo 'publish: no artifacts for $(VERSION); run make build'; exit 1; }
	@if [ -z "$(YES)" ]; then \
		printf 'upload %s %s to %s.\ntype the version to confirm: ' "$(DIST)" "$(VERSION)" "$(REPO)"; \
		read ans; [ "$$ans" = "$(VERSION)" ] || { echo 'aborted'; exit 1; }; \
	fi
	$(TWINE) upload --repository $(REPO) $(WHL) $(SDIST)

push:  ## push the branch and the tag
	git push origin HEAD
	git push origin $(TAG)

dry: $(GATES)  ## run every check, ship nothing
	@printf 'dry run ok. `make release` ships %s\n' "$(VERSION)"

# Publish before push, so a public tag always points at a released version.
# The reverse order can leave a public tag that PyPI never received.
release: $(GATES) tag publish push  ## the whole recipe
	@printf 'released %s %s to %s\n' "$(DIST)" "$(VERSION)" "$(REPO)"
