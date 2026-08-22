# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for Trove Accounts Hub.

    pip install pyinstaller
    pyinstaller TroveAccountsHub.spec        # from the repository root

It produces one self-contained executable in `dist/`. The user needs nothing
else installed: no Python, no dependencies. On Windows the interface is drawn by
the Microsoft Edge WebView2 runtime, which ships with Windows 11 and arrives
with Edge on Windows 10 - if it is missing, `main._fatal` says so and links to
it rather than failing silently.

Two things worth knowing about this file:

  * The whole `web/` folder travels as data, not as code. `core.paths.base_dir`
    already resolves it through `sys._MEIPASS` when frozen, so nothing in the
    application needs to know whether it is packaged or not.

  * pywebview registers its own PyInstaller hook through the `pyinstaller40`
    entry point, so its platform backends are collected automatically. That is
    why there is no long `hiddenimports` list here.
"""

import sys
from pathlib import Path

NAME = "TroveAccountsHub"
ROOT = Path(SPECPATH)
WINDOWS = sys.platform == "win32"

# The interface, and the Win32 helper the Linux launch path drives inside the
# Wine prefix. The helper is 59 KB and is carried on both platforms so that one
# recipe covers both.
datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "native"), "native"),
]

# Trimming what is never imported. `tkinter` in particular drags in the whole Tk
# runtime for nothing: pywebview never touches it here.
excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test",
    "pytest", "playwright", "PIL", "fontTools",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX trips antivirus heuristics for no real gain
    runtime_tmpdir=None,
    # No console window: this is a desktop application, and a black terminal
    # behind it looks like something went wrong. Start-up failures go to a
    # message box instead - see main._fatal.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "web" / "img" / "app.ico") if WINDOWS else None,
)
