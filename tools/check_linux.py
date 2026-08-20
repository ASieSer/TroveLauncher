"""¿Está este equipo listo para usar Trove Accounts Hub en Linux?

Dice, en una pantalla, qué falta y qué no. Pensado para pegar la salida cuando
algo no arranca, en vez de ir adivinando.

    python tools/check_linux.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WIDTH = 22
problems: list[str] = []


def line(label: str, value: str, ok: bool | None = None) -> None:
    mark = "  " if ok is None else ("OK" if ok else "!!")
    print(f"{mark} {label:<{WIDTH}} {value}")


def fail(text: str) -> None:
    problems.append(text)


print(f"Trove Accounts Hub — Linux readiness check\n{'-' * 62}")
line("python", sys.version.split()[0], sys.version_info >= (3, 10))
if sys.version_info < (3, 10):
    fail("Python 3.10 or newer is required.")

# --- la ventana ------------------------------------------------------------
try:
    import webview  # noqa: F401
    line("pywebview", "installed", True)
except ImportError:
    line("pywebview", "NOT installed", False)
    fail("pip install -r requirements.txt")

engine = ""
try:
    import gi

    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2  # noqa: F401
    engine = "WebKitGTK 4.1"
except Exception:
    try:
        import gi

        gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2  # noqa: F401
        engine = "WebKitGTK 4.0"
    except Exception:
        pass
if engine:
    line("window engine", engine, True)
else:
    line("window engine", "none visible from this Python", False)
    fail("sudo apt install python3-gi gir1.2-webkit2-4.1\n"
         "     Using a virtualenv? Create it with --system-site-packages:\n"
         "     `gi` is a system package and a plain venv does NOT see it.")

# --- lanzar el juego -------------------------------------------------------
from core import winehost  # noqa: E402

wine = winehost.find_wine()
line("wine", wine or "not found", bool(wine))
if not wine:
    fail("sudo apt install wine64      (or point at your Wine in Settings → Wine)")
else:
    try:
        version = subprocess.run([wine, "--version"], capture_output=True,
                                 text=True, timeout=30).stdout.strip()
        line("wine version", version or "?", True)
    except (OSError, subprocess.SubprocessError):
        line("wine version", "not answering", False)
        fail("Wine is installed but will not run; try `wine --version` by hand.")

helper = winehost.helper_path()
line("Wine helper", helper.name if helper.is_file() else "MISSING", helper.is_file())
if not helper.is_file():
    fail(f"{helper} is missing. It ships pre-built in the repository; if the\n"
         f"     binary was deleted, rebuild it with tools/build_helper.sh "
         f"(needs mingw-w64).")

# --- secretos --------------------------------------------------------------
from core import vault  # noqa: E402

# Se elige el almacén en silencio: su aviso ya sale abajo, con el resto.
vault.vault(log=lambda *a: None)
status = vault.status()
line("keyring", status["backend"], status["available"])
if not status["available"]:
    fail(f"{status['detail']}\n"
         "     The app still works, but it will not remember passwords and you\n"
         "     will have to sign in again on every start. On a normal desktop\n"
         "     (GNOME, KDE) this is already sorted.")

# --- el juego --------------------------------------------------------------
from core import installs  # noqa: E402

found = installs.detect()
if found:
    line("game installs", f"{len(found)} found", True)
    for item in found:
        print(f"     · [{item['kind']}] {item['path']}")
else:
    line("game installs", "none detected", False)
    fail("Trove was not found. It is looked for inside Proton prefixes\n"
         "     (steamapps/compatdata/*/pfx) and Wine ones (~/.wine, Lutris,\n"
         "     Bottles). If yours lives elsewhere, add it by hand from the app.")

print("-" * 62)
if problems:
    print(f"{len(problems)} thing(s) to sort out:\n")
    for i, text in enumerate(problems, 1):
        print(f"  {i}. {text}")
    sys.exit(1)
print("All set: python main.py")
