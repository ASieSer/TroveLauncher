"""Encontrar las instalaciones de Trove del equipo.

Tres orígenes, en este orden:

  1. Registro de Windows -> ``Uninstall\\Glyph Trove*`` -> ``InstallLocation``.
  2. Steam -> ``libraryfolders.vdf`` -> ``steamapps/common/Trove/Games/Trove/*``.
  3. Carpetas que el usuario añade a mano (guardadas en prefs).

Una carpeta sólo cuenta como instalación si contiene un ejecutable de Trove
válido, lo que comprobamos leyendo la cabecera PE (ejecutable GUI de Windows) en
lugar de fiarnos del nombre. La lógica de validación viene de BetterTroveTools
(MIT, (c) 2026-Present Aallyn Reed).

El escaneo toca el registro y el disco, así que se cachea durante la vida del
proceso; ``invalidate()`` lo descarta cuando el usuario cambia sus carpetas.
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

# Sufijo que cuelga de cualquier raíz donde viva Glyph.
_GLYPH_SUFFIX = Path("Glyph") / "Games" / "Trove"

_CACHE: list | None = None
_CACHE_LOCK = threading.Lock()


def invalidate() -> None:
    """Descarta el escaneo cacheado; el siguiente ``detect()`` vuelve a mirar."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def is_scanned() -> bool:
    """¿Hay ya un escaneo hecho? Permite a la interfaz arrancar sin esperar.

    En caliente el escaneo tarda unos milisegundos, pero con los discos dormidos
    llega a tardar varios segundos (medido: 9,5 s despertando un disco mecánico),
    y eso no puede bloquear la carga de la ventana.
    """
    with _CACHE_LOCK:
        return _CACHE is not None


# --- validación del ejecutable ---------------------------------------------


def _is_gui_executable(path: Path) -> tuple[bool, bool]:
    """(es un .exe GUI de Windows, es 64 bits) leyendo sólo la cabecera PE."""
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
    except OSError:
        return False, False


def _looks_like_trove(path: Path) -> bool:
    """Para un .exe con nombre no estándar: ¿lleva dentro el nombre de Trove?"""
    try:
        content = path.read_bytes()
    except OSError:
        return False
    markers = [b"Trove.exe", b"Trove_x64.exe",
               "Trove.exe".encode("utf-16-le"), "Trove_x64.exe".encode("utf-16-le")]
    return any(m in content for m in markers)


def find_executable(game_dir: Path) -> Path | None:
    """El ejecutable de Trove dentro de ``game_dir``, o None si no hay ninguno.

    Camino rápido: los nombres canónicos ya identifican al juego, así que basta
    con validar la cabecera (evitamos leer los ~21 MB del exe). Se prefiere el
    binario de 64 bits. Si nada coincide, se recorre cada .exe buscando el
    identificador dentro del fichero.
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


# --- clasificación Live / PTS ----------------------------------------------


def classify(path: Path, name: str = "") -> str:
    """'pts' si la carpeta (o su nombre) es la del servidor de pruebas, si no 'live'.

    Comparamos segmentos completos de la ruta, no subcadenas, para no marcar
    como PTS algo como ``.../scripts/...``.
    """
    if re.search(r"\bpts\b", name or "", re.IGNORECASE):
        return "pts"
    if any(seg.lower() == "pts" for seg in Path(path).parts):
        return "pts"
    return "live"


# --- Steam ------------------------------------------------------------------


def _steam_library_paths(steam_root: Path) -> list[Path]:
    """Rutas de biblioteca declaradas en libraryfolders.vdf.

    Extraemos las claves ``"path"`` con una expresión regular en lugar de
    depender de un parser VDF completo: es el único dato que necesitamos y el
    formato de esa línea es estable.
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


# --- Registro de Windows ----------------------------------------------------


def _registry_values(root_path: str, prefix: str, value_name: str) -> list[str]:
    """Lee ``value_name`` de cada subclave de ``root_path`` cuyo nombre empiece
    por ``prefix``, en ambos hives y con y sin WOW6432Node."""
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
    """Unidades locales fijas. Excluimos red y extraíbles: una unidad de red
    lenta convertiría el escaneo de arranque en una espera de varios segundos."""
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
    """Busca ``<unidad>:\\[carpeta\\]Glyph\\Games\\Trove`` en las unidades fijas.

    Existe porque el registro no siempre sabe dónde está Glyph: una instalación
    movida, copiada o sin desinstalador (comprobado en la práctica) no deja
    ninguna clave, y entonces el usuario tenía que añadir su carpeta a mano.

    Sólo miramos la raíz de cada unidad y un nivel por debajo, que es donde la
    gente pone sus carpetas de juegos (``E:\\Juegos\\Glyph\\...``). Es un listado
    por unidad más un stat por carpeta de primer nivel: barato, y nada parecido
    a recorrer el disco entero.
    """
    found: list[Path] = []
    for drive in _fixed_drives():
        candidates = [drive / _GLYPH_SUFFIX]
        try:
            candidates += [entry / _GLYPH_SUFFIX for entry in drive.iterdir()]
        except OSError:
            pass  # unidad sin permisos o no lista: seguimos con la siguiente
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


# --- API pública ------------------------------------------------------------


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
    return found


def detect(custom_dirs: list | None = None) -> list[dict]:
    """Todas las instalaciones conocidas: las detectadas (cacheadas) más las que
    el usuario ha añadido a mano, que siempre se revalidan por si desaparecen."""
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
