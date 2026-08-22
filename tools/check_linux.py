"""Is this machine ready to run Trove Accounts Hub on Linux?

Says, on one screen, what is missing and what is not. Meant for pasting the
output when something will not start, instead of guessing.

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

# --- the window ------------------------------------------------------------
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

# --- the game --------------------------------------------------------------
from core import installs, prefs, winehost  # noqa: E402

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

# The prefix decides the runner: inside a Proton prefix the game launches with
# that Proton, so we have to know which it is before asking about Wine.
data = prefs.load()
chosen = data.get("game_path") or (found[0]["path"] if found else "")
prefix = winehost.prefix_for(Path(chosen) if chosen else None,
                             data.get("wine_prefix", ""))
line("prefix", prefix)

# --- what it launches with ------------------------------------------------------
wine = winehost.find_wine(data.get("wine_binary", ""), prefix)
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

runners = winehost.find_proton_runners()
if runners:
    line("proton found", f"{len(runners)} runner(s)", True)
    for runner in runners:
        mark = " ← in use" if runner["wine"] == wine else ""
        print(f"     · {runner['name']}: {runner['wine']}{mark}")

# The failure that kills launches on Linux with nothing to explain it: the
# anti-cheat loader imports a symbol this Wine does not have, the game never
# even opens and the launcher sits at "Logging in".
if wine and winehost.missing_loader_symbol(wine):
    line(f"{winehost.LOADER_SYMBOL}", "MISSING from this Wine", False)
    fail(f"This Wine build does not export {winehost.LOADER_SYMBOL}, which\n"
         "     Trove's anti-cheat loader imports: it dies with a \"procedure entry\n"
         "     point\" dialog and the game never starts.\n"
         + ("     Use one of the Proton runners listed above: set it in\n"
            "     Settings → Wine, or install the game inside a Proton prefix."
            if runners else
            "     Install Proton from Steam (any recent version ships it) and\n"
            "     point Settings → Wine at its …/files/bin/wine."))
elif wine:
    line(f"{winehost.LOADER_SYMBOL}", "present", True)

# --- secrets --------------------------------------------------------------
from core import vault  # noqa: E402

# The store is picked quietly: its warning already appears below with the rest.
vault.vault(log=lambda *a: None)
status = vault.status()
line("keyring", status["backend"], status["available"])
if not status["available"]:
    fail(f"{status['detail']}\n"
         "     The app still works, but it will not remember passwords and you\n"
         "     will have to sign in again on every start. On a normal desktop\n"
         "     (GNOME, KDE) this is already sorted.")

# --- the helper, FOR REAL --------------------------------------------------
#
# The file existing does not mean Wine can run it: the prefix may belong to
# another runner, or have someone else's wineserver going. It is started against
# the prefix the game lives in, which is the one launching will use.
helper = winehost.helper_path()
if not helper.is_file():
    line("Wine helper", "MISSING", False)
    fail(f"{helper} is missing. It ships pre-built in the repository; if the\n"
         f"     binary was deleted, rebuild it with tools/build_helper.sh "
         f"(needs mingw-w64).")
elif wine:
    if chosen:
        print(f"     · launching from {chosen}")
    probe = winehost.WineHelper(wine=wine, prefix=prefix, log=lambda *a: None)
    try:
        probe.start()
        pid = probe.call("ping")[0]
        seen = len(probe.list_processes())
        line("Wine helper", f"answers in {prefix}", True)
        print(f"     · pid {pid} inside the prefix, sees {seen} processes")
    except Exception as exc:
        line("Wine helper", "does NOT run", False)
        fail(f"The helper cannot run inside {prefix}:\n     "
             + str(exc).replace("\n", "\n     "))
    finally:
        probe.stop()

print("-" * 62)
if problems:
    print(f"{len(problems)} thing(s) to sort out:\n")
    for i, text in enumerate(problems, 1):
        print(f"  {i}. {text}")
    sys.exit(1)
print("All set: python main.py")
