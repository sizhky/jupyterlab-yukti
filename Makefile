# Release recipe for jupyterlab-yukti.
#
# One command ships a version:
#   make release PART=minor MSG="what you changed"
#
# Split of duties:
#   checkpoint (~/.zshrc)  promotes branches   feature/* -> dev -> main
#   this file              ships one version   commit -> bump -> verify -> PyPI
#
# Three rules this file keeps, which `checkpoint --publish` does not:
#   1. It commits your work only when you name it. MSG is the opt-in, so a
#      `git add -A` can never surprise you.
#   2. Every step fails loud. No step hides an error to keep going.
#   3. It holds no branch policy. Ship from whatever branch you choose.

# Make keeps the blanks before an inline `#`, so every comment sits above its line.
DIST := jupyterlab-yukti
# Labextension directory. It must match the npm package name, not the PyPI name.
EXT := yukti
# A ~/.pypirc section, or `pypi`, or `testpypi`.
REPO ?= sizhky
# patch | minor | major | none
PART ?= patch
# Message for your uncommitted work. Unset means "refuse to commit it".
MSG ?=

# Read once per make run. `release` re-enters make after `bump`, so the later
# steps read the version that `bump` just wrote, not the one it replaced.
VERSION := $(shell uv version --short 2>/dev/null)
NPMVER  := $(shell node -p "require('./package.json').version" 2>/dev/null)
TAG     := v$(VERSION)
RELENV  := .venv-release
# Scope every glob to this version, so a stale dist/ can never be uploaded.
WHL     := dist/*-$(VERSION)-*.whl
SDIST   := dist/*-$(VERSION).tar.gz
VFILES  := pyproject.toml uv.lock package.json package-lock.json
GATES   := guard test build check verify
# Isolated, so the recipe ignores whatever the active environment holds.
TWINE := uvx twine
SUB   := $(MAKE) --no-print-directory
# macOS Finder drops a .DS_Store back into a directory while `rm -rf` walks it,
# so the first pass can fail with "Directory not empty". Retry, then warn.
# Removing scratch files must never fail a release whose checks already passed.
SCRUB = rm -rf $(1) 2>/dev/null || rm -rf $(1) 2>/dev/null \
	|| echo 'warn: could not remove $(1); `make clean` will retry'

SHELL := /bin/bash
.NOTPARALLEL:
.PHONY: help bump stage clean guard test build check verify tag publish push dry ship release

help:  ## show this help
	@printf '\n  %s %s  ->  %s\n\n' "$(DIST)" "$(VERSION)" "$(REPO)"
	@grep -hE '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /|/' \
		| awk -F'|' '{printf "  make %-9s %s\n", $$1, $$2}'
	@printf '\n  knobs: PART=%s REPO=%s MSG=%s\n' "$(PART)" "$(REPO)" "$(MSG)"
	@printf '  usage: make release PART=minor MSG="what you changed"\n\n'

bump:  ## write the new version to the files, no git (PART=patch/minor/major/none)
	@if [ '$(PART)' = none ]; then echo 'bump: skipped, staying on $(VERSION)'; exit 0; fi; \
	uv version --no-sync --bump $(PART) \
		&& npm version "$$(uv version --short)" --no-git-tag-version --allow-same-version >/dev/null \
		&& echo "bump: $(VERSION) -> $$(uv version --short)"

# One commit, not two. The bump lands inside the commit that describes the work,
# because the tag is already the release marker and does not need a second one.
stage:  ## bump, then commit the work and the bump together
	@if [ -n "$$(git status --porcelain)" ] && [ -z '$(MSG)' ]; then \
		echo 'stage: the tree is dirty, and I will not guess a message.'; \
		echo '  commit it yourself, or re-run with MSG="what you changed"'; \
		exit 1; \
	fi
	@$(SUB) bump
	@M='$(MSG)'; M="$${M:-release v$$(uv version --short)}"; \
	if [ -n "$$(git status --porcelain)" ]; then \
		git add -A && git commit -q -m "$$M" && echo "stage: committed \"$$M\""; \
	else \
		echo 'stage: nothing to commit'; \
	fi

clean:  ## delete every build output
	@$(call SCRUB,dist build *.egg-info $(RELENV))
	npm run clean

guard:  ## refuse a release that cannot succeed
	@test -n "$(VERSION)" || { echo 'guard: cannot read the version'; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo 'guard: tree is dirty; pass MSG='; exit 1; }
	@test "$(VERSION)" = "$(NPMVER)" \
		|| { echo 'guard: pyproject $(VERSION) != package.json $(NPMVER)'; exit 1; }
	@! git rev-parse -q --verify refs/tags/$(TAG) >/dev/null \
		|| { echo 'guard: tag $(TAG) exists; raise PART'; exit 1; }
	@echo 'guard: ok, shipping $(VERSION)'

test:  ## run the fast test suite
	uv run --no-sync pytest -q

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
	@$(call SCRUB,$(RELENV))

tag:  ## create the annotated tag, locally only
	git tag -a $(TAG) -m "$(DIST) $(VERSION)"

publish:  ## upload to REPO -- PyPI never reuses a version, so this is final
	@ls $(WHL) $(SDIST) >/dev/null 2>&1 \
		|| { echo 'publish: no artifacts for $(VERSION); run make build'; exit 1; }
	@echo 'publish: uploading $(DIST) $(VERSION) to $(REPO)'
	$(TWINE) upload --repository $(REPO) $(WHL) $(SDIST)

push:  ## push the branch and the tag in one atomic step
	git push --atomic -u origin HEAD $(TAG)

dry: $(GATES)  ## run every check, ship nothing
	@printf 'dry run ok. `make release PART=none` ships %s as is\n' "$(VERSION)"

# Publish before push, so a public tag always points at a released version.
# The reverse order can leave a public tag that PyPI never received.
ship: $(GATES) tag publish push  ## gates, tag, publish, push -- no bump
	@printf 'released %s %s to %s\n' "$(DIST)" "$(VERSION)" "$(REPO)"

# Two sub-makes, because make reads the version once per run. `ship` must start
# after `stage` has written and committed the new one.
release:  ## stage then ship, in one commit
	@$(SUB) stage
	@$(SUB) ship
