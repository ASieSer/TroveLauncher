"""Where everything the application writes to disk lives."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "TroveAccountsHub"

# Things worth telling the user that happen before there is a window to tell
# them in: adopting the previous data folder, recovering prefs.json from its
# backup. Packaged there is no console either, so printing alone would lose
# them. They wait here until the interface asks for its first state and
# ``LauncherService.state`` drains them into the log panel.
_NOTES: list[str] = []


def note(message: str) -> None:
    """Record a start-up message, and print it if anyone is listening."""
    _NOTES.append(message)
    if sys.stdout is not None:
        print(message)


def drain_notes() -> list[str]:
    """Take the pending start-up messages. They are only reported once."""
    pending = list(_NOTES)
    _NOTES.clear()
    return pending

# Names the folder had before, newest first. The first one found with data
# inside is adopted (see ``_adopt_legacy``).
LEGACY_APP_DIR_NAMES = ("TroveLauncher",)

# Records that the adoption happened, so it is not repeated if the user wipes
# their prefs.json and deliberately starts over.
_ADOPTED_MARK = "adopted-from.txt"

_adoption_checked = False


def _roaming_dir() -> Path:
    """Root the application folder lives under, without creating it."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    xdg = os.getenv("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _adopt_legacy(new: Path) -> None:
    """Bring over the data from the previously named folder, exactly once.

    The application was renamed from Trove Launcher to Trove Accounts Hub. The
    folder follows the name, but inside it are the accounts, tickets and
    passwords of anyone already using it: without this, the first renamed
    version would look empty to them, like a fresh install.

    It **copies**, it does not move: if someone goes back to an earlier version,
    the old folder is still there with its data. The price is a duplicate on
    disk, which is exactly what being able to go back costs.

    It never overwrites a file that already exists at the destination and never
    interrupts start-up: if the copy fails halfway, what was brought over stays
    and the rest remains in the old folder.

    The DPAPI blobs (``auth-*.bin``, ``cred-*.bin``) decrypt just the same from
    the new path: they are tied to the Windows user and the machine, not to the
    folder. That is why the password entropy still reads ``TroveLauncher...`` —
    it is an identifier, not a user-visible name, and changing it would render
    already-saved passwords unreadable.
    """
    global _adoption_checked
    if _adoption_checked:
        return
    _adoption_checked = True

    if (new / _ADOPTED_MARK).exists() or (new / "prefs.json").exists():
        return

    for name in LEGACY_APP_DIR_NAMES:
        old = new.parent / name
        if old == new or not (old / "prefs.json").exists():
            continue
        copied = 0
        try:
            for src in sorted(old.iterdir()):
                dst = new / src.name
                if dst.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                copied += 1
        except OSError as exc:
            note(f"[paths] incomplete adoption from {name}: {exc}")
        try:
            (new / _ADOPTED_MARK).write_text(
                f"{copied} items copied from {old}\n", encoding="utf-8")
        except OSError:
            pass
        note(f"[paths] adopted data from {old} ({copied} items); "
             f"the previous folder is left untouched")
        return


def app_data_dir() -> Path:
    """Per-user base folder for everything we store.

    Windows: ``%APPDATA%/TroveAccountsHub``. On other platforms we fall back to
    ``$XDG_DATA_HOME`` (or ``~/.local/share``), where only the updating side
    makes sense on its own - launching the game needs Win32 or Wine.
    """
    d = _roaming_dir() / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    _adopt_legacy(d)
    return d


def trove_appdata_dir() -> Path:
    """The ``%APPDATA%/Trove`` folder the game itself uses (ModCfgs lives there).

    We do not create it off Windows: the game is not installed natively there.
    """
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Trove"
    return Path.home() / ".local" / "share" / "Trove"


def prefs_path() -> Path:
    return app_data_dir() / "prefs.json"


def macaddr_path() -> Path:
    return app_data_dir() / "macaddr.txt"


def base_dir() -> Path:
    """The application root: what ``web/`` and ``native/`` hang off.

    Frozen with PyInstaller, the data does not live next to the executable but in
    the folder it prepares and announces in ``sys._MEIPASS`` (``_internal/`` in a
    folder bundle, a temporary directory in a one-file one). Looking next to the
    executable worked with PyInstaller 5 and stopped working in 6.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent
