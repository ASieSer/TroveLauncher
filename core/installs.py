"""Finding the machine's Trove installations.

Four sources, in this order:

  1. The Windows registry -> ``Uninstall\\Glyph Trove*`` -> ``InstallLocation``.
  2. Steam -> ``libraryfolders.vdf`` -> ``steamapps/common/Trove/Games/Trove/*``.
  3. Wine and Proton prefixes (Linux only): the game is a Windows one, so there
     it lives inside a prefix, under ``drive_c``.
  4. Folders the user adds by hand (stored in prefs).

A folder only counts as an installation if it contains a valid Trove
executable, which we check by reading the PE header (a Windows GUI executable)
rather than trusting the name. The validation logic comes from BetterTroveTools
(MIT, (c) 2026-Present Aallyn Reed).

The scan touches the registry and the disk, so it is cached for the life of the
process; ``invalidate()`` discards it when the user changes their folders.
"""

from __future__ import annotations

import ctypes
import os
import re
import string
import struct
import threading
from pathlib import Path

if os.name == "nt":
    import winreg

# The suffix hanging off any root Glyph lives under.
_GLYPH_SUFFIX = Path("Glyph") / "Games" / "Trove"

_CACHE: list | None = None
_CACHE_LOCK = threading.Lock()


def invalidate() -> None:
    """Discards the cached scan; the next ``detect()`` looks again."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def is_scanned() -> bool:
    """Has a scan been done yet? Lets the interface start without waiting.

    Warm, the scan takes a few milliseconds, but with sleeping disks it can take
    several seconds (measured: 9.5 s waking a mechanical drive), and that cannot
    block the window from loading.
    """
    with _CACHE_LOCK:
        return _CACHE is not None


# --- validating the executable ---------------------------------------------


def _is_gui_executable(path: Path) -> tuple[bool, bool]:
    """(is a Windows GUI .exe, is 64-bit) from the PE header alone."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False, False
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset)
            if f.read(4) != b"PE\0\0":
                return False, False
            machine = struct.unpack("<H", f.read(2))[0]
            f.seek(pe_offset + 22)
            characteristics = struct.unpack("<H", f.read(2))[0]
            if not (characteristics & 0x0002):     # IMAGE_FILE_EXECUTABLE_IMAGE
                return False, False
            f.seek(pe_offset + 24 + 68)
            subsystem = struct.unpack("<H", f.read(2))[0]
            if subsystem != 2:                     # IMAGE_SUBSYSTEM_WINDOWS_GUI
                return False, False
            return True, machine == 0x8664
    except (OSError, struct.error):
        # A half-written .exe (an interrupted download) has no header and the
        # unpacking blows up. That is not a valid executable, which is exactly
        # what was being asked; it is not an exception worth raising.
        return False, False


def _looks_like_trove(path: Path) -> bool:
    """For a non-standard .exe name: does it carry Trove's name inside?"""
    try:
        content = path.read_bytes()
    except OSError:
        return False
    markers = [b"Trove.exe", b"Trove_x64.exe",
               "Trove.exe".encode("utf-16-le"), "Trove_x64.exe".encode("utf-16-le")]
    return any(m in content for m in markers)


def find_executable(game_dir: Path) -> Path | None:
    """Trove's executable inside ``game_dir``, or None if there is none.

    Fast path: the canonical names already identify the game, so validating the
    header is enough (we avoid reading the exe's ~21 MB). The 64-bit binary is
    preferred. If nothing matches, every .exe is walked looking for the marker
    inside the file.
    """
    game_dir = Path(game_dir)
    for name in ("Trove_x64.exe", "Trove.exe"):
        candidate = game_dir / name
        if candidate.is_file() and _is_gui_executable(candidate)[0]:
            return candidate

    fallback = None
    try:
        exes = sorted(game_dir.glob("*.exe"))
    except OSError:
        return None
    for exe in exes:
        ok, is64 = _is_gui_executable(exe)
        if not ok or not _looks_like_trove(exe):
            continue
        if is64:
            return exe
        fallback = fallback or exe
    return fallback


def is_valid_install(path: Path) -> bool:
    path = Path(path)
    return path.is_dir() and find_executable(path) is not None


# --- Live / PTS classification ----------------------------------------------


def classify(path: Path, name: str = "") -> str:
    """'pts' if the folder (or its name) is the test server's, else 'live'.

    Whole path segments are compared, not substrings, so something like
    ``.../scripts/...`` is not marked as PTS.
    """
    if re.search(r"\bpts\b", name or "", re.IGNORECASE):
        return "pts"
    if any(seg.lower() == "pts" for seg in Path(path).parts):
        return "pts"
    return "live"


# --- Steam ------------------------------------------------------------------


def _steam_library_paths(steam_root: Path) -> list[Path]:
    """Library paths declared in libraryfolders.vdf.

    The ``"path"`` keys are pulled out with a regular expression rather than
    depending on a full VDF parser: it is the only field we need and the format
    of that line is stable.
    """
    vdf_file = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[Path] = []
    for raw in re.findall(r'"path"\s+"([^"]+)"', text):
        candidate = Path(raw.replace("\\\\", "\\"))
        if candidate not in out:
            out.append(candidate)
    return out


def _trove_dirs_under_steam(steam_root: Path) -> list[Path]:
    found = []
    for library in _steam_library_paths(steam_root):
        root = library / "steamapps" / "common" / "Trove" / "Games" / "Trove"
        if not root.is_dir():
            continue
        try:
            for sub in root.iterdir():
                if sub.is_dir() and is_valid_install(sub):
                    found.append(sub)
        except OSError:
            continue
    return found


# --- The Windows registry ----------------------------------------------------


def _registry_values(root_path: str, prefix: str, value_name: str) -> list[str]:
    """Reads ``value_name`` from every subkey of ``root_path`` whose name starts
    with ``prefix``, in both hives and with and without WOW6432Node."""
    out: list[str] = []
    if os.name != "nt":
        return out
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for node in ("", "WOW6432Node\\"):
            full = f"SOFTWARE\\{node}{root_path}"
            try:
                key = winreg.OpenKeyEx(hive, full)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    if not sub.startswith(prefix):
                        continue
                    try:
                        with winreg.OpenKeyEx(hive, full + sub) as subkey:
                            value = winreg.QueryValueEx(subkey, value_name)[0]
                        if value and value not in out:
                            out.append(value)
                    except OSError:
                        continue
            finally:
                winreg.CloseKey(key)
    return out


def _fixed_drives() -> list[Path]:
    """Fixed local drives. Network and removable ones are excluded: a slow
    network drive would turn the start-up scan into a wait of several seconds."""
    if os.name != "nt":
        return []
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return []
    for index, letter in enumerate(string.ascii_uppercase):
        if not bitmask & (1 << index):
            continue
        root = Path(f"{letter}:\\")
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(str(root)) == 3:  # DRIVE_FIXED
                drives.append(root)
        except Exception:
            continue
    return drives


def _scan_drives_for_glyph() -> list[Path]:
    """Looks for ``<drive>:\\[folder\\]Glyph\\Games\\Trove`` on the fixed drives.

    This exists because the registry does not always know where Glyph is: an
    installation that was moved, copied or came without an uninstaller (seen in
    practice) leaves no key at all, and then the user had to add their folder by
    hand.

    Only each drive's root and one level below it are looked at, which is where
    people keep their game folders (``E:\\Games\\Glyph\\...``). That is one
    listing per drive plus one stat per first-level folder: cheap, and nothing
    like walking the whole disk.
    """
    found: list[Path] = []
    for drive in _fixed_drives():
        candidates = [drive / _GLYPH_SUFFIX]
        try:
            candidates += [entry / _GLYPH_SUFFIX for entry in drive.iterdir()]
        except OSError:
            pass  # drive not readable or not ready: on to the next
        for root in candidates:
            try:
                if not root.is_dir():
                    continue
                for sub in root.iterdir():
                    if sub.is_dir() and is_valid_install(sub):
                        found.append(sub)
            except OSError:
                continue
    return found


def _glyph_dirs() -> list[Path]:
    dirs = []
    for raw in _registry_values(
        "Microsoft\\Windows\\CurrentVersion\\Uninstall\\", "Glyph Trove", "InstallLocation"
    ):
        path = Path(raw)
        if is_valid_install(path):
            dirs.append(path)
    dirs.extend(_scan_drives_for_glyph())
    return dirs


def _wine_prefixes() -> list[Path]:
    """Prefixes that may hold an installed Trove, on Linux.

    Two families: Proton's, one per game, under
    ``steamapps/compatdata/<appid>/pfx``; and plain Wine ones, usually
    ``~/.wine`` or Lutris/Bottles folders. They are not hunted across the whole
    disk: only in the places convention puts them.
    """
    if os.name == "nt":
        return []
    home = Path.home()
    out: list[Path] = []
    for root in _steam_roots():
        for library in [root] + _steam_library_paths(root):
            compat = library / "steamapps" / "compatdata"
            try:
                out += [entry / "pfx" for entry in compat.iterdir() if entry.is_dir()]
            except OSError:
                continue
    for base in (home / ".wine",
                 home / "Games",
                 home / ".local" / "share" / "wineprefixes",
                 home / ".var" / "app" / "net.lutris.Lutris" / "data" / "lutris" / "runners",
                 home / ".local" / "share" / "lutris" / "runners"):
        if (base / "drive_c").is_dir():
            out.append(base)
            continue
        try:
            out += [entry for entry in base.iterdir() if (entry / "drive_c").is_dir()]
        except OSError:
            continue
    return out


def _trove_dirs_under_prefixes() -> list[Path]:
    """Glyph installations inside a prefix.

    Inside, the layout is Windows's, so we look where we would look on a disk:
    ``<drive_c>/Glyph/Games/Trove/*`` and the same hanging off
    ``Program Files``/``Program Files (x86)``, which is where Glyph's installer
    leaves it.
    """
    found: list[Path] = []
    for prefix in _wine_prefixes():
        drive_c = prefix / "drive_c"
        bases = [drive_c, drive_c / "Program Files", drive_c / "Program Files (x86)"]
        for base in bases:
            root = base / _GLYPH_SUFFIX
            try:
                if not root.is_dir():
                    continue
                for sub in root.iterdir():
                    if sub.is_dir() and is_valid_install(sub):
                        found.append(sub)
            except OSError:
                continue
    return found


def _steam_roots() -> list[Path]:
    roots = []
    for value_name in ("InstallPath", "SteamPath"):
        for raw in _registry_values("Valve\\", "Steam", value_name):
            roots.append(Path(raw))
    if os.name != "nt":
        home = Path.home()
        roots += [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]
    return roots


# --- public API ------------------------------------------------------------


def _entry(path: Path, source: str, name: str | None = None) -> dict:
    path = Path(path)
    label = name or f"({source.capitalize()}) {path.name}"
    return {
        "path": str(path),
        "name": label,
        "source": source,
        "kind": classify(path, label),
    }


def _scan() -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def _push(entry: dict) -> None:
        key = str(Path(entry["path"]).resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(entry)

    for path in _glyph_dirs():
        _push(_entry(path, "glyph"))
    for root in _steam_roots():
        for path in _trove_dirs_under_steam(root):
            _push(_entry(path, "steam"))
    for path in _trove_dirs_under_prefixes():
        _push(_entry(path, "wine"))
    return found


def detect(custom_dirs: list | None = None) -> list[dict]:
    """Every known installation: the detected (cached) ones plus those the user
    added by hand, which are always revalidated in case they have gone."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = _scan()
        out = list(_CACHE)

    seen = {str(Path(e["path"]).resolve()).lower() for e in out}
    for item in custom_dirs or []:
        raw = item.get("path") if isinstance(item, dict) else item
        if not raw:
            continue
        path = Path(raw)
        if not is_valid_install(path):
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        label = (item.get("name") if isinstance(item, dict) else "") or path.name
        out.append(_entry(path, "custom", f"(Personalizada) {label}"))
    return out
