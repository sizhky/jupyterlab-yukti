# Publishing Yukti

Publish Yukti once on PyPI. JupyterLab 4 uses PyPI as its default extension
catalog and installs with `pip`, so there is no second upload to Jupyter.

The recipe is a `Makefile`. It runs on your machine.

## Two tools, one seam

| Tool                     | Owns                                    |
| ------------------------ | --------------------------------------- |
| `checkpoint` (`~/.zshrc`) | Branch promotion: `feature/* -> dev -> main` |
| `make` (this repo)       | One version: build, verify, release     |

Keep them apart. Use `checkpoint main "message"` to promote, then `make release`
to ship. Do not use `checkpoint --publish` on this repo, for two reasons:

1. Its publish steps run through `_ckp_try`, which prints a note and continues
   when a step fails. A failed build still reaches `twine upload`.
2. It ran `uv version --bump` against a dynamic version, which `uv` refuses.
   `_ckp_try` hid that error, so the bump never happened.

Both are fixed here. The version is now static, and every `make` step fails loud.

## Normal release

```
make bump              # 0.0.4 -> 0.0.5 in pyproject.toml, uv.lock, package.json
checkpoint main "..."  # or plain git: commit the bump
make dry               # every check, ships nothing
make release           # the real thing
```

`make bump` and `make release` must be separate commands. Make reads the version
once when it starts, so a bump in the same run would go unseen.

## The recipe

Run `make` with no target to see this list.

| Step      | Action                                                       |
| --------- | ------------------------------------------------------------ |
| `bump`    | Raise the version in `pyproject.toml`, `uv.lock`, `package.json`. |
| `clean`   | Delete every build output.                                   |
| `guard`   | Refuse a dirty tree, a version mismatch, or a used tag.      |
| `build`   | Build the labextension, then the sdist and the wheel.        |
| `check`   | `twine check --strict` on both artifacts.                    |
| `verify`  | Install the wheel in a throwaway env and prove it loads.     |
| `tag`     | Create the annotated tag `v<version>`, locally.              |
| `publish` | Upload to `REPO`. Prompts for the version first.             |
| `push`    | Push the branch and the tag.                                 |
| `dry`     | `guard build check verify`. Ships nothing.                   |
| `release` | `dry` plus `tag publish push`.                               |

Three knobs, no flags:

- `REPO=sizhky` — a `~/.pypirc` section, or `pypi`, or `testpypi`
- `PART=patch` — for `make bump`
- `YES=1` — skip the upload prompt, for unattended runs

Every step runs alone. `make verify` after a `make build` is the useful pair.

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
version. The reverse order can leave a public tag that PyPI never received,
and deleting a public tag is worse than pushing one late.

## First release

Upload to TestPyPI first. It is the one rehearsal you get.

```
make dry
make publish REPO=testpypi
```

Then install from TestPyPI in a scratch environment and open JupyterLab. When
that looks right, run `make release`.

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
