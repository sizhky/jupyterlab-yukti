from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from IPython.core.interactiveshell import InteractiveShell


__version__ = '0.0.4'

def load_ipython_extension(ipython: InteractiveShell):
    from .ask import YuktiMagics

    ipython.register_magics(YuktiMagics)


def unload_ipython_extension(ipython: InteractiveShell):
    pass
