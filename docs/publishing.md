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

It raises the version, commits that together with your work as **one** commit,
runs every gate, then publishes and pushes. `PART` is `patch`, `minor`, `major`, or `none`. `none` ships the current
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

### One commit, not two

`stage` bumps the version and then makes a single commit holding both your work
and the bump. Tools like `npm version` and `cargo release` make a separate
`release vX.Y.Z` commit, but they split for a reason that does not apply here: a
bot bumps the version after the feature branch has already merged. Releasing
from your own machine has no such order. The tag is already the release marker,
so a marker commit only doubles the graph.

When the tree is clean, there is nothing of yours to describe, so the message
defaults to `release vX.Y.Z` and `MSG` is not needed.

## The recipe

Run `make` with no target to see this list.

| Step      | Action                                                        |
| --------- | ------------------------------------------------------------- |
| `bump`    | Write the new version to `pyproject.toml`, `uv.lock`, `package.json`. No git. |
| `stage`   | Bump, then commit your work and the bump as **one** commit. Needs `MSG` when the tree is dirty. |
| `clean`   | Delete every build output.                                    |
| `guard`   | Refuse a dirty tree, a version mismatch, or a used tag.       |
| `test`    | Run the fast test suite.                                      |
| `build`   | Build the labextension, then the sdist and the wheel.         |
| `check`   | `twine check --strict` on both artifacts.                     |
| `verify`  | Install the wheel in a throwaway env and prove it loads.      |
| `tag`     | Create the annotated tag `v<version>`, locally.               |
| `publish` | Upload to `REPO`. Announces what it is uploading, then does it. |
| `push`    | Push the branch and the tag in one atomic step.               |
| `dry`     | `guard test build check verify`. Ships nothing.               |
| `ship`    | `dry` plus `tag publish push`. No bump.                       |
| `release` | `stage`, then `ship`.                                         |

Three knobs, no flags:

- `PART=patch` — `patch`, `minor`, `major`, or `none`
- `MSG=` — commit message for your work; unset means "refuse to commit it"
- `REPO=sizhky` — a `~/.pypirc` section, or `pypi`, or `testpypi`

Every step runs alone. `make build verify` is the useful pair while developing.

`release` re-enters `make` twice. Make reads the version when it starts, so
`ship` has to begin after `stage` has written the new one.

### No confirmation prompt

`publish` does not ask you to confirm. Seven gates run before it: a clean tree,
matching versions, an unused tag, the test suite, the build, `twine check
--strict`, and a clean-environment install. A prompt that fires on every release
adds nothing to that, and one you see every time is one you stop reading.

`make dry` is where caution belongs. It runs every gate and ships nothing.

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

Nothing has reached PyPI yet, and the name `jupyterlab-yukti` is free, so ship
the version already in the tree rather than raising it again:

```
make release PART=none MSG="what you changed"
```

The remote `sizhky/jupyterlab-yukti` stays empty until this runs. `release`
pushes the branch and the tag together at the end, which fills it.

A TestPyPI rehearsal is the textbook advice, and it needs a `[testpypi]` section
in `~/.pypirc` that you do not have yet:

```
make dry
make publish REPO=testpypi
```

Skip it if you want. Its value here is small, because `verify` already installs
the real wheel in a clean environment and reads back `enabled OK`, and
`twine check --strict` already validated how the README will render.

## When a step fails

Read which step failed, then resume from there. Nothing reaches PyPI before
`publish`, so any earlier failure is safe.

Once `stage` has run, the new version is committed. Do **not** re-run `release`
with a `PART`, or you will skip a version number for no reason. Resume with
`ship`, which runs every gate and then publishes without bumping:

```
make ship
```

If the failure left the tree dirty, `release` with `PART=none` does the same
thing and commits the fix on the way through:

```
make release PART=none MSG="fix the thing that failed"
```

This split is the reason `ship` exists apart from `release`.

### Cleanup never fails a release

`verify` builds a throwaway environment and deletes it afterwards. On macOS,
Finder writes a `.DS_Store` back into a directory while `rm -rf` is walking it,
so the delete can fail with `Directory not empty` even though every check
passed. Deleting scratch files is hygiene, not a gate, so it retries once and
then warns:

```
warn: could not remove .venv-release; `make clean` will retry
```

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
