"""Dónde vive todo lo que la aplicación escribe en disco."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "TroveAccountsHub"

# Nombres que tuvo la carpeta antes, del más reciente al más antiguo. La primera
# que aparezca con datos dentro se adopta (ver ``_adopt_legacy``).
LEGACY_APP_DIR_NAMES = ("TroveLauncher",)

# Deja constancia de la adopción para no repetirla si el usuario borra su
# prefs.json y empieza de cero a propósito.
_ADOPTED_MARK = "adopted-from.txt"

_adoption_checked = False


def _roaming_dir() -> Path:
    """Raíz donde vive la carpeta de la aplicación, sin crearla."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    xdg = os.getenv("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _adopt_legacy(new: Path) -> None:
    """Trae los datos de la carpeta del nombre anterior, una sola vez.

    La aplicación pasó de llamarse Trove Launcher a Trove Accounts Hub. La
    carpeta sigue el nombre, pero dentro están las cuentas, los tickets y las
    contraseñas de quien ya la usaba: sin esto, la primera versión renombrada le
    aparecería vacía, como una instalación nueva.

    Se **copia**, no se mueve: si alguien vuelve a una versión anterior, la
    carpeta vieja sigue donde estaba y con sus datos. El precio es un duplicado
    en disco, que es justo lo que cuesta poder dar marcha atrás.

    Nunca pisa un fichero que ya exista en destino y nunca interrumpe el
    arranque: si la copia falla a medias, lo que se haya traído se queda y el
    resto sigue en la carpeta vieja.

    Los blobs DPAPI (``auth-*.bin``, ``cred-*.bin``) se descifran igual desde la
    ruta nueva: van atados al usuario de Windows y a la máquina, no a la
    carpeta. Por eso la entropía de las contraseñas sigue diciendo
    ``TroveLauncher...``, que es un identificador y no un nombre a la vista:
    cambiarla dejaría ilegibles las contraseñas ya guardadas.
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
            print(f"[paths] adopción incompleta desde {name}: {exc}")
        try:
            (new / _ADOPTED_MARK).write_text(
                f"{copied} elementos copiados desde {old}\n", encoding="utf-8")
        except OSError:
            pass
        print(f"[paths] datos adoptados desde {old} ({copied} elementos); "
              f"la carpeta anterior se deja intacta")
        return


def app_data_dir() -> Path:
    """Carpeta base, por usuario, para todo lo que guardamos.

    Windows: ``%APPDATA%/TroveAccountsHub``. En otras plataformas caemos a
    ``$XDG_DATA_HOME`` (o ``~/.local/share``), donde sólo tiene sentido la parte
    de actualización — lanzar el juego requiere Win32.
    """
    d = _roaming_dir() / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    _adopt_legacy(d)
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
