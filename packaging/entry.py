"""Entry point for the frozen (PyInstaller) build.

``winmonitor/__main__.py`` cannot be used directly: it imports with a relative
``from .cli import main``, which only works when the package is imported, not
when a file is run as a script.  This module is the plain absolute-import
equivalent that PyInstaller can analyse.
"""

from __future__ import annotations

from winmonitor.cli import main

if __name__ == "__main__":
    main()
