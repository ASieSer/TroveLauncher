"""Prueba de extremo a extremo de la entrega del ticket bajo Wine.

Esto es lo que no se puede dar por bueno leyendo el código: que el ayudante
Win32 (`native/troveinject.c`) monte el blob del ticket donde el juego lo busca,
y que el juego pueda recogerlo duplicando los handles DEL PROCESO DEL LANZADOR.
Aquí se comprueba con un Trove de mentira que hace exactamente esos pasos
(`tools/wine_test/fakegame.c`) y con un loader de anti-cheat de mentira que
imita al de XIGNCODE: recibe el nombre del juego en argv[1], lo arranca y se
muere (`tools/wine_test/fakeloader.c`).

Hace falta Wine y mingw-w64:

    sudo apt install wine64 mingw-w64
    python tools/test_wine_helper.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.rift import clean_ticket  # noqa: E402
from core.winehost import WineHelper, WineError  # noqa: E402

TICKET = (
    "1234\n"
    "Signature: abc123==\n"
    "<?xml version=\"1.0\"?><ticket><user>mulero@example.com</user>"
    "<payload>ñáéí unicode y espacios</payload></ticket>\n"
)
AUTH = "[AuthServer] Address = ams-c12-b01.ams.triongames.com:6560"

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)


def need(tool):
    if not shutil.which(tool):
        print(f"falta {tool}: instala wine64 y mingw-w64 para correr esta prueba")
        sys.exit(2)


need("wine")
need("x86_64-w64-mingw32-gcc")

tmp = Path(tempfile.mkdtemp(prefix="troveinject-test-"))
prefix = tmp / "pfx"
game_dir = tmp / "game"
game_dir.mkdir(parents=True)
env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all")

print("compilando…")
subprocess.run([str(ROOT / "tools/build_helper.sh")], check=True, cwd=ROOT,
               stdout=subprocess.DEVNULL)
subprocess.run(["x86_64-w64-mingw32-gcc", "-Os", "-s", "-o",
                str(game_dir / "Trove_x64.exe"),
                str(ROOT / "tools/wine_test/fakegame.c")], check=True)
subprocess.run(["x86_64-w64-mingw32-gcc", "-Os", "-s", "-o",
                str(tmp / "xldr_Trove_GL_loader_x64.exe"),
                str(ROOT / "tools/wine_test/fakeloader.c")], check=True)

print("preparando el prefijo (tarda un poco la primera vez)…")
subprocess.run(["wine", "wineboot", "-i"], env=env, check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)

logs: list[str] = []
helper = WineHelper(wine=shutil.which("wine"), prefix=str(prefix),
                    log=lambda m: logs.append(str(m)))
try:
    helper.start()
    check("el ayudante arranca dentro del prefijo", helper.alive)
    check("y saluda por el canal de registro",
          any("ayudante listo" in m for m in logs))

    procs = helper.list_processes()
    check("enumera procesos del prefijo",
          len(procs) > 0 and all(len(p) == 3 for p in procs))

    def run_case(label, with_loader, stay=False):
        """Lanza el juego falso y devuelve (resultado, ticket recibido)."""
        out = game_dir / f"got-{label}.txt"
        loader = game_dir / "xldr_Trove_GL_loader_x64.exe"
        if with_loader:
            shutil.copy(tmp / "xldr_Trove_GL_loader_x64.exe", loader)
        else:
            loader.unlink(missing_ok=True)
        helper._path_cache.clear()
        win_out = helper.to_windows_path(out)
        # El juego hereda el entorno del ayudante, así que hay que reiniciarlo
        # para cambiárselo. Es cosa del test; la aplicación no lo necesita.
        helper.stop()
        os.environ["FAKEGAME_OUT"] = win_out
        os.environ["FAKEGAME_STAY"] = "1" if stay else ""
        if not stay:
            os.environ.pop("FAKEGAME_STAY", None)
        helper.start()
        res = helper.spawn(game_dir / "Trove_x64.exe", TICKET, AUTH, wait_ms=20000)
        for _ in range(40):
            if out.exists():
                break
            time.sleep(0.25)
        got = out.read_text(encoding="utf-8") if out.exists() else ""
        auth_file = out.with_suffix(out.suffix + ".auth")
        got_auth = auth_file.read_text(encoding="utf-8") if auth_file.exists() else ""
        return res, got, got_auth

    # --- 1) lanzamiento directo, sin loader -------------------------------
    res, got, got_auth = run_case("directo", with_loader=False)
    check("lanza sin loader", res["via_loader"] is False and res["pid"] > 0)
    check("el juego señala el evento (ticket consumido)", res["consumed"] is True)
    # La comparación fuerte: lo que el juego ha descifrado tiene que ser
    # EXACTAMENTE lo que la implementación de Windows habría metido en el blob.
    # Son dos códigos distintos —Python y C— y aquí se demuestra que coinciden
    # byte a byte, recorte de cabecera incluido.
    check("el ticket descifrado coincide con el que armaría Windows",
          got == clean_ticket(TICKET))
    check("y conserva el unicode", "ñáéí unicode y espacios" in got)
    check("y con él la cadena de servidores de autenticación", got_auth == AUTH)
    check("la cabecera previa a Signature: se recorta, como en Windows",
          not got.startswith("1234") and got.startswith("Signature:"))

    code = helper.wait_for_exit(res["pid"])
    check("wait devuelve el código de salida del juego", code == 7)

    # --- 2) a través del loader del anti-cheat ----------------------------
    res, got, _ = run_case("loader", with_loader=True)
    check("detecta el loader y lanza a través de él", res["via_loader"] is True)
    check("con loader, el ticket llega igual de intacto", got == clean_ticket(TICKET))
    check("y se vigila el juego, no el loader que ya murió",
          res["pid"] > 0 and res["consumed"] is True)
    live = {pid for pid, _ppid, name in helper.list_processes()
            if name.lower() == "trove_x64.exe"}
    check("el pid resuelto es el de un Trove_x64.exe de verdad",
          res["pid"] in live or helper.wait_for_exit(res["pid"]) == 7)

    # --- 3) matar una partida ---------------------------------------------
    res, _, _ = run_case("kill", with_loader=False, stay=True)
    check("una partida que no termina sigue viva", res["pid"] > 0)
    check("se puede cerrar", helper.terminate(res["pid"]) is True)
    code = helper.wait_for_exit(res["pid"])
    check("y wait se entera de que murió", code is not None and code != 7)

    # --- 4) errores legibles ----------------------------------------------
    try:
        helper.spawn(game_dir / "NoExiste.exe", TICKET, AUTH, wait_ms=2000)
        check("un ejecutable inexistente da error", False)
    except WineError as exc:
        check("un ejecutable inexistente da error legible",
              "CreateProcess" in str(exc))

    helper.stop()
    check("el ayudante se cierra al pedírselo", not helper.alive)
finally:
    helper.stop()
    subprocess.run(["wineserver", "-k"], env=env, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
