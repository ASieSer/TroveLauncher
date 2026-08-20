"""Elegir el Wine correcto en Linux: el del prefijo, no el que haya en el PATH.

El Wine del sistema no siempre exporta ``WSCEnumProtocols32``, y sin ese símbolo
el loader del anti-cheat de Trove muere con un diálogo de «procedure entry point»
y el juego no llega a abrirse. Proton sí lo trae. Aquí se monta un Steam de
mentira —con sus Proton y su prefijo de compatdata— y se comprueba que la
elección del runner sale sola y que el aviso salta cuando toca.

    python tools/test_proton_runner.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="troveproton-"))
os.environ["HOME"] = str(TMP / "home")
os.environ.pop("WINE", None)
Path(os.environ["HOME"]).mkdir(parents=True)

from core import winehost  # noqa: E402

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)


def make_runner(folder: Path, layout: str, symbol: bool) -> Path:
    """Un Proton de mentira: su wine y su ws2_32.dll con o sin el símbolo."""
    binary = folder / layout / "bin" / "wine"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho wine-fake\n", encoding="utf-8")
    binary.chmod(0o755)
    dll = folder / layout / "lib" / "wine" / "x86_64-windows" / "ws2_32.dll"
    dll.parent.mkdir(parents=True)
    names = b"WSAStartup\x00WSCEnumProtocols\x00"
    if symbol:
        names += winehost.LOADER_SYMBOL.encode("ascii") + b"\x00"
    dll.write_bytes(b"MZ" + b"\x00" * 128 + names)
    return binary


# --- un Steam de mentira ----------------------------------------------------
steam = Path(os.environ["HOME"]) / ".steam" / "steam"
common = steam / "steamapps" / "common"
common.mkdir(parents=True)
(steam / "steamapps" / "libraryfolders.vdf").write_text(
    '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n}\n' % steam, encoding="utf-8")

old_proton = make_runner(common / "Proton 8.0", "dist", symbol=True)
new_proton = make_runner(common / "Proton 9.0", "files", symbol=True)
ge = make_runner(steam / "compatibilitytools.d" / "GE-Proton10-4", "files", symbol=True)
# Y un Wine del sistema al que le falta lo que el anti-cheat pide.
system_wine = make_runner(TMP / "usr", "", symbol=False)

runners = winehost.find_proton_runners(fresh=True)
names = [r["name"] for r in runners]
check("encuentra los Proton de la biblioteca y los de la comunidad",
      set(names) == {"Proton 8.0", "Proton 9.0", "GE-Proton10-4"})
check("y los ordena del más nuevo al más viejo", names[0] == "GE-Proton10-4")
check("con el binario correcto según la versión de Proton",
      {r["wine"] for r in runners} == {str(old_proton), str(new_proton), str(ge)})

# --- el prefijo dice con qué se hizo ----------------------------------------
compat = steam / "steamapps" / "compatdata" / "123450"
pfx = compat / "pfx" / "drive_c"
pfx.mkdir(parents=True)
(compat / "config_info").write_text(
    f"{common / 'Proton 9.0'}/files/share/fonts/\n"
    f"{common / 'Proton 9.0'}/files/lib64/wine\n", encoding="utf-8")
check("de un prefijo de Proton se deduce SU Proton",
      winehost.proton_for_prefix(compat / "pfx") == str(new_proton))

# Sin config_info queda el nombre suelto del fichero `version`.
(compat / "config_info").unlink()
(compat / "version").write_text("1699999999 GE-Proton10-4\n", encoding="utf-8")
check("y si no, por el nombre que deja Steam en `version`",
      winehost.proton_for_prefix(compat / "pfx") == str(ge))

# Un prefijo de Wine a secas no es de nadie: ahí no hay Proton que deducir.
plain = TMP / "home" / ".wine"
(plain / "drive_c").mkdir(parents=True)
check("un prefijo de Wine normal no inventa un runner",
      winehost.proton_for_prefix(plain) == "")

# --- la elección final ------------------------------------------------------
os.environ["PATH"] = str(system_wine.parent) + os.pathsep + os.environ.get("PATH", "")
check("dentro de un prefijo de Proton se lanza con ese Proton",
      winehost.find_wine("", str(compat / "pfx")) == str(ge))
check("fuera de él, el del sistema",
      winehost.find_wine("", str(plain)) == str(system_wine))
check("y lo que diga el usuario manda sobre todo lo demás",
      winehost.find_wine(str(old_proton), str(compat / "pfx")) == str(old_proton))

# --- el aviso del símbolo ---------------------------------------------------
check("se ve que al Wine del sistema le falta el símbolo del anti-cheat",
      winehost.missing_loader_symbol(str(system_wine)) is True)
check("y que a Proton no", winehost.missing_loader_symbol(str(ge)) is False)
check("de lo que no se puede saber, no se avisa",
      winehost.missing_loader_symbol(str(TMP / "no" / "bin" / "wine")) is False)

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
