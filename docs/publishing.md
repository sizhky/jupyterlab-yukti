# Publishing Yukti

Publish Yukti once on PyPI. JupyterLab 4 uses PyPI as its default extension
catalog and installs with `pip`, so there is no second upload to Jupyter.

The recipe is a `Makefile`. It runs on your machine.

## Two tools, one seam

| Tool                     | Owns                                    |
| ------------------------ | --------------------------------------- |
| `checkpoint` (`~/.zshrc`) | Branch promotion: `feature/* -> dev -> main` |
| `make` (this repo)       | One version: build, verify, release     |

`checkpoint` still owns the branch graph. `make release` never merges and never
switches branches; it commits, tags, and pushes wherever you already stand. Do not use `checkpoint --publish` on this repo, for two reasons:

1. Its publish steps run through `_ckp_try`, which prints a note and continues
   when a step fails. A failed build still reaches `twine upload`.
2. It ran `uv version --bump` against a dynamic version, which `uv` refuses.
   `_ckp_try` hid that error, so the bump never happened.

Both are fixed here. The version is now static, and every `make` step fails loud.

## Normal release

One command:

```
make release PART=minor MSG="what you changed"
```

It commits your work, raises the version, runs every gate, then publishes and
pushes. `PART` is `patch`, `minor`, `major`, or `none`. `none` ships the current
version without raising it, which is what a first release wants.

`MSG` is the opt-in for `git add -A`. Leave it out and the recipe refuses:

```
commit: the tree is dirty, and I will not guess a message.
  commit it yourself, or re-run with MSG="what you changed"
```

That is deliberate. `checkpoint` commits everything under a timestamp when you
give it no message, so work lands in a release with no record of what it was.
Here you either name the change or commit it yourself first. If your tree is
already clean, `MSG` is not needed.

Check first, ship nothing:

```
make dry
```

## The recipe

Run `make` with no target to see this list.

| Step      | Action                                                        |
| --------- | ------------------------------------------------------------- |
| `commit`  | Commit your work. Needs `MSG` when the tree is dirty.         |
| `bump`    | Raise the version in `pyproject.toml`, `uv.lock`, `package.json`, then commit that alone. |
| `clean`   | Delete every build output.                                    |
| `guard`   | Refuse a dirty tree, a version mismatch, or a used tag.       |
| `test`    | Run the fast test suite.                                      |
| `build`   | Build the labextension, then the sdist and the wheel.         |
| `check`   | `twine check --strict` on both artifacts.                     |
| `verify`  | Install the wheel in a throwaway env and prove it loads.      |
| `tag`     | Create the annotated tag `v<version>`, locally.               |
| `publish` | Upload to `REPO`. Prompts for the version first.              |
| `push`    | Push the branch and the tag in one atomic step.               |
| `dry`     | `guard test build check verify`. Ships nothing.               |
| `ship`    | `dry` plus `tag publish push`. No bump.                       |
| `release` | `commit`, `bump`, then `ship`.                                |

Four knobs, no flags:

- `PART=patch` — `patch`, `minor`, `major`, or `none`
- `MSG=` — commit message for your work; unset means "refuse to commit it"
- `REPO=sizhky` — a `~/.pypirc` section, or `pypi`, or `testpypi`
- `YES=1` — skip the upload prompt, for unattended runs

Every step runs alone. `make build verify` is the useful pair while developing.

`release` re-enters `make` three times, once per stage. Make reads the version
when it starts, so `ship` has to begin after `bump` has written the new one.

## Why this order

`guard` is first because PyPI never reuses a version number. A wrong version
cannot be undone, so the free checks run before any build work.

`verify` uses a throwaway environment, not your dev environment. Your dev
environment already holds an editable install, which hides a missing
shared-data path in the wheel. This step is what proves the wheel works:

```
import ok: 0.0.4
        yukti v0.0.4 enabled OK (python, jupyterlab-yukti)
```

`publish` runs before `push`. A public tag then always points at a released
version. The push is atomic, so the branch and the tag land together or not at all. The reverse order can leave a public tag that PyPI never received,
and deleting a public tag is worse than pushing one late.

## First release

`0.0.4` has never reached PyPI, and the name `jupyterlab-yukti` is free, so ship
the current version rather than raising it:

```
make release PART=none MSG="add the release recipe"
```

The remote `sizhky/jupyterlab-yukti` is empty until this runs. `release` pushes
the branch and the tag together at the end, which fills it.

A TestPyPI rehearsal is the textbook advice, and it needs a `[testpypi]` section
in `~/.pypirc` that you do not have yet:

```
make dry
make publish REPO=testpypi
```

Skip it if you want. Its value here is small, because `verify` already installs
the real wheel in a clean environment and reads back `enabled OK`, and
`twine check --strict` already validated how the README will render.

## Packaging changes made for this

1. The PyPI name is `jupyterlab-yukti`. The name `yukti` is already owned. The
   Python import stays `yukti`.
2. The version is static in `pyproject.toml`. `yukti/__init__.py` now reads it
   with `importlib.metadata.version`, so there is one source, not two. This is
   what `uv version --bump` needs.
3. `pyproject.toml` carries the three JupyterLab classifiers. The `Prebuilt` one
   makes the package visible in JupyterLab's Extension Manager.
4. `yukti_frontend/install.json` names `jupyterlab-yukti`, so JupyterLab knows
   which Python package to uninstall.

The labextension directory keeps the name `yukti`, not `jupyterlab-yukti`. That
name must match the npm package name in `package.json`, not the PyPI name.

## Industry standards, and the one this skips

Current PyPA practice, and where this recipe stands:

| Standard                                            | Here |
| --------------------------------------------------- | ---- |
| PEP 621 metadata in `pyproject.toml`                | yes  |
| Static version, read at runtime via `importlib.metadata` | yes  |
| Build with a PEP 517 frontend, never `setup.py`     | yes, `uv build` |
| Ship both an sdist and a wheel                      | yes  |
| `twine check --strict` before upload                | yes  |
| Rehearse on TestPyPI                                | yes, `REPO=testpypi` |
| Tag every release `vX.Y.Z`                          | yes  |
| Keep a changelog                                    | `CHANGELOG.md` |
| **Upload from CI with a Trusted Publisher (OIDC)**   | **no** |

The last row is the real gap. PyPI recommends Trusted Publishers over API
tokens, because an OIDC token lives for minutes and cannot leak from a laptop.
This recipe uploads with your `~/.pypirc` token instead.

That is a fair trade for a solo project, and it is reversible. Every step above
`publish` already runs anywhere, so a GitHub Actions job would call the same
targets and swap one line. Move when you add a second maintainer.

## Stale CI

`.github/workflows/` still holds three `jupyter_releaser` workflows copied from
another project. They do not serve this recipe:

- `check-release.yml` runs on every push to `main` and uploads an artifact named
  after a different project.
- `publish-release.yml` needs a GitHub App (`APP_ID`, `APP_PRIVATE_KEY`) and an
  `NPM_TOKEN` this project never sets.

Delete them once `make release` has shipped once:

```
git rm -r .github/workflows
```

## Installing the released package

```
pip install jupyterlab-yukti
```

Restart JupyterLab, authenticate the Codex CLI, then run `%load_ext yukti`.

## Sources

- [Python Packaging User Guide: packaging projects](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [Python Packaging User Guide: single-sourcing the version](https://packaging.python.org/en/latest/discussions/single-source-version/)
- [PyPI: Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
- [PyPI: using TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/)
- [JupyterLab: prebuilt extension distribution](https://jupyterlab.readthedocs.io/en/4.4.x/extension/extension_dev.html)
- [uv: project versions](https://docs.astral.sh/uv/reference/cli/#uv-version)
- [Existing `yukti` project on PyPI](https://pypi.org/project/yukti/)
