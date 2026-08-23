from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from IPython.core.interactiveshell import InteractiveShell


# Read the version from the installed distribution, so pyproject.toml stays the
# single source. Pro: `uv version --bump` needs no second edit here.
# Con: costs one metadata lookup at import, and reads 0.0.0.dev0 when the
# package is not installed at all.
try:
    __version__ = version('jupyterlab-yukti')
except PackageNotFoundError:
    __version__ = '0.0.0.dev0'

def load_ipython_extension(ipython: InteractiveShell):
    from .ask import YuktiMagics

    ipython.register_magics(YuktiMagics)


def unload_ipython_extension(ipython: InteractiveShell):
    pass
