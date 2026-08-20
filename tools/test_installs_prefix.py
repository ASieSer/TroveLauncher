"""Comprueba que en Linux se encuentra el Trove que vive dentro de un prefijo.

El juego es de Windows: en Linux está dentro de un prefijo de Proton
(``steamapps/compatdata/<appid>/pfx``) o de Wine, bajo ``drive_c``. Aquí se
monta esa estructura con un ejecutable de Windows de verdad —el binario del
juego falso, que tiene cabecera PE GUI de 64 bits— y se comprueba que la
detección lo encuentra y que el prefijo se deduce de vuelta desde su ruta, que
es lo que después decide dónde se lanza.

    python tools/test_installs_prefix.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)


if not shutil.which("x86_64-w64-mingw32-gcc"):
    print("falta mingw-w64: hace falta un .exe de Windows real para validar")
    sys.exit(2)

tmp = Path(tempfile.mkdtemp(prefix="troveinstalls-"))
exe_src = ROOT / "tools" / "wine_test" / "fakegame.c"
game_exe = tmp / "Trove_x64.exe"
# -mwindows lo marca como ejecutable GUI, que es lo que mira la validación.
subprocess.run(["x86_64-w64-mingw32-gcc", "-Os", "-s", "-mwindows",
                "-o", str(game_exe), str(exe_src)], check=True)

home = tmp / "home"
steam = home / ".steam" / "steam"
(steam / "steamapps").mkdir(parents=True)
(steam / "steamapps" / "libraryfolders.vdf").write_text(
    '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n}\n' % steam, encoding="utf-8")

# Un Trove de Glyph instalado dentro del prefijo de Proton de otro juego, que es
# como acaba estando cuando se instala Glyph con Proton.
pfx = steam / "steamapps" / "compatdata" / "1234560" / "pfx"
live = pfx / "drive_c" / "Program Files (x86)" / "Glyph" / "Games" / "Trove" / "Live"
pts = pfx / "drive_c" / "Program Files (x86)" / "Glyph" / "Games" / "Trove" / "PTS"
for folder in (live, pts):
    folder.mkdir(parents=True)
    shutil.copy(game_exe, folder / "Trove_x64.exe")

# Y otro en un prefijo de Wine a secas.
plain = home / ".wine"
plain_live = plain / "drive_c" / "Glyph" / "Games" / "Trove" / "Live"
plain_live.mkdir(parents=True)
shutil.copy(game_exe, plain_live / "Trove_x64.exe")

os.environ["HOME"] = str(home)
try:
    import core.installs as installs
    import core.winehost as winehost
    importlib.reload(installs)
    importlib.reload(winehost)

    check("el ejecutable de prueba pasa por Trove de verdad",
          installs.is_valid_install(live))

    prefixes = [str(p) for p in installs._wine_prefixes()]
    check("encuentra el prefijo de Proton", str(pfx) in prefixes)
    check("y el prefijo de Wine a secas", str(plain) in prefixes)

    found = installs.detect()
    paths = {entry["path"] for entry in found}
    check("detecta la instalación Live del prefijo de Proton", str(live) in paths)
    check("detecta también la PTS", str(pts) in paths)
    check("y la del prefijo de Wine", str(plain_live) in paths)
    check("las marca como venidas de un prefijo",
          all(e["source"] == "wine" for e in found))
    check("y distingue Live de PTS",
          {e["kind"] for e in found if e["path"] == str(pts)} == {"pts"}
          and {e["kind"] for e in found if e["path"] == str(live)} == {"live"})

    # Lo que cierra el círculo: de la ruta del juego se vuelve al prefijo donde
    # hay que lanzarlo. Con el prefijo equivocado, el juego ni siquiera existe.
    check("de la ruta del juego se deduce el prefijo de Proton",
          winehost.prefix_for(live / "Trove_x64.exe") == str(pfx))
    check("y el del prefijo de Wine",
          winehost.prefix_for(plain_live) == str(plain))
    check("lo que diga el usuario manda sobre la deducción",
          winehost.prefix_for(live, "/otro/sitio") == "/otro/sitio")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
