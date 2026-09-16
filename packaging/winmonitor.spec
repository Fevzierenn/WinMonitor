# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: a single winmonitor.exe that needs no Python installed.

Build it with::

    python -m PyInstaller packaging/winmonitor.spec --noconfirm

The result is ``dist/winmonitor.exe``.

Notes on what has to be pulled in by hand
-----------------------------------------
*The stylesheet.*  ``WinMonitorApp.CSS_PATH`` points at ``app.tcss`` next to
``ui/app.py``.  PyInstaller only bundles ``.py`` files, so the stylesheet is
added as data at the same relative path it lives at in the source tree.

*Textual's widgets.*  Several are imported lazily by name, so static analysis
misses them; ``collect_submodules`` brings the whole widget package in.

*Textual's own data.*  It ships stylesheets and a theme catalogue alongside its
code, which ``collect_data_files`` picks up.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller

datas = [
    # The UI stylesheet, at the path CSS_PATH resolves to.
    (str(ROOT / "winmonitor" / "ui" / "app.tcss"), "winmonitor/ui"),
]
datas += collect_data_files("textual")
datas += collect_data_files("rich")

hiddenimports = [
    *collect_submodules("textual.widgets"),
    *collect_submodules("winmonitor"),
    "textual.app",
    "textual.containers",
    "textual.screen",
    "textual.timer",
    "textual.widgets.data_table",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here is used, and each one drags in a large dependency tree.
    excludes=[
        "tkinter",
        "unittest",
        "pydoc_data",
        "test",
        "distutils",
        "setuptools",
        "pip",
        "numpy",
        "PIL",
        "matplotlib",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="winmonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is a common false-positive trigger for AV.
    runtime_tmpdir=None,
    console=True,  # It is a terminal application; it must keep its console.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
