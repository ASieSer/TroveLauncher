"""Launcher core: talking to Trion's CDN, authenticating and starting Trove.

Modules ported from BetterTroveTools (MIT, (c) 2026-Present Aallyn Reed) — see
LICENSE-BetterTroveTools and NOTICE.md at the root of the project:

  * ``cdn``        - update-CDN client plus pointer/manifest parsers.
  * ``updater``    - keeps an installation current (delta download, state in sqlite).
  * ``trionauth``  - Glyph credentials -> a ticket ready to launch, cached in the vault.
  * ``inject``     - hands that ticket to Trove_x64.exe the way Glyph does.
  * ``launch``     - the per-region auth-server string.

Our own modules:

  * ``paths``      - where everything we write goes.
  * ``prefs``      - preferences, saved accounts and groups.
  * ``vault``      - the system secret store: DPAPI on Windows, the desktop keyring elsewhere.
  * ``rift``       - the RC4 + "RIFT" ticket blob, shared by both launch paths.
  * ``installs``   - finding Trove installations (registry, Steam, custom folders).
  * ``gamehost``   - one interface for launching here or inside Wine.
  * ``winehost``   - the Linux end: picks the runner and drives the Win32 helper.
  * ``service``    - orchestrator: worker thread, 2FA, auto-relog, progress to the UI.

Only ``requests`` + the standard library + ctypes are needed. The launch path is
Windows-only (it uses the Win32 process and handle APIs) unless Wine is in play;
the updater on its own works anywhere.
"""

# The one place the version is written. It reaches the status bar through
# ``LauncherService.state``, so the number on screen cannot drift from the
# release tag the way a string pasted into the HTML could.
__version__ = "0.2.1"
