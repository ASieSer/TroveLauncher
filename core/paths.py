"""Dónde vive todo lo que el launcher escribe en disco."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "TroveLauncher"


def app_data_dir() -> Path:
    """Carpeta base, por usuario, para todo lo que guardamos.

    Windows: ``%APPDATA%/TroveLauncher``. En otras plataformas caemos a
    ``$XDG_DATA_HOME`` (o ``~/.local/share``), donde sólo tiene sentido la parte
    de actualización — lanzar el juego requiere Win32.
    """
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        d = Path(base) / APP_DIR_NAME
    else:
        xdg = os.getenv("XDG_DATA_HOME")
        d = (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def trove_appdata_dir() -> Path:
    """La carpeta ``%APPDATA%/Trove`` que usa el propio juego (ahí viven ModCfgs).

    No la creamos si no existe fuera de Windows: allí el juego no está instalado.
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
    """Raíz de la aplicación (para localizar ``web/``), tanto en fuente como congelada."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent
