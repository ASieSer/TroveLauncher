"""Lanzar y vigilar el juego, aquí o dentro de Wine.

El resto de la aplicación no debería saber en qué sistema corre. Este módulo
ofrece las cuatro cosas que hacen falta para una partida —lanzarla, saber
cuándo termina, cerrarla y mirar qué procesos hay— y por debajo hay dos
implementaciones:

``NativeHost``
    Windows. Llama a ``inject.py`` directamente: la aplicación ES el lanzador
    que el juego consulta para recoger el ticket.

``WineHost``
    Linux. El lanzador no puede ser un proceso Linux (ver ``winehost.py``), así
    que hace de intermediario con el ayudante que corre dentro del prefijo.

Las dos hablan de "pid" y las dos devuelven lo mismo. La diferencia es de quién
es ese pid: en Windows es un pid del sistema; en Linux, uno de Wine. Como todas
las operaciones sobre él pasan por el mismo anfitrión que lo dio, esa diferencia
no se escapa de aquí.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from . import prefs


class HostUnavailable(RuntimeError):
    """No se puede lanzar en este equipo, con una razón que enseñar."""


class GameHost:
    kind = "none"

    def check(self) -> None:
        """Lanza ``HostUnavailable`` si hoy no se puede jugar."""
        raise HostUnavailable("this system cannot launch Trove")

    def spawn(self, exe: Path, ticket: str, auth_server: str, *,
              parent_process_name: str = "", exclude: set[int] | None = None,
              log=print) -> int:
        """Lanza y devuelve el pid del JUEGO, no el de lo que se ejecutó.

        ``exclude`` son las partidas que la aplicación ya vigila: con el loader
        del anti-cheat por medio hay que salir a buscar el proceso del juego, y
        sin esta lista dos cuentas lanzadas a la vez pueden acabar apuntando a
        la misma.
        """
        raise HostUnavailable("this system cannot launch Trove")

    def wait_for_exit(self, pid: int) -> int | None:
        return None

    def terminate(self, pid: int) -> bool:
        return False

    def pids_by_name(self, name: str) -> set[int]:
        return set()

    def list_processes(self) -> list[tuple[int, int, str]]:
        """``(pid, pid del padre, nombre)`` de lo que corre en este anfitrión."""
        return []

    def wait_until_ready(self, pid: int, timeout: float = 120.0, log=print) -> bool:
        """Espera a que la partida termine de arrancar. False si se murió.

        Lanzar la siguiente cuenta mientras la anterior todavía se está
        levantando es lo que hace que el loader del anti-cheat se caiga sin
        lanzar nada, así que quien lanza espera aquí.
        """
        return True

    def close(self) -> None:
        pass

    def status(self) -> dict:
        """Para la interfaz: si se puede jugar y, si no, por qué.

        ``warning`` es para lo que se puede lanzar pero probablemente salga mal,
        que no es lo mismo que no poder: ver ``WineHost.status``.
        """
        try:
            self.check()
            return {"kind": self.kind, "ready": True, "detail": ""}
        except HostUnavailable as exc:
            return {"kind": self.kind, "ready": False, "detail": str(exc)}


# --- Windows ----------------------------------------------------------------


class NativeHost(GameHost):
    kind = "windows"

    def check(self) -> None:
        return

    def spawn(self, exe: Path, ticket: str, auth_server: str, *,
              parent_process_name: str = "", exclude: set[int] | None = None,
              log=print) -> int:
        from . import inject

        # Lo que ya corría con ese nombre, más lo que ya vigilamos.
        before = inject.pids_by_name(exe.name) | set(exclude or ())
        result = inject.spawn(exe, ticket, auth_server,
                              parent_process_name=parent_process_name or None,
                              log=log)

        # El loader del anti-cheat se muere a veces sin llegar a lanzar nada
        # (código 1021, por ejemplo). Si además nadie ha recogido el ticket, este
        # lanzamiento no ha dejado ninguna partida: buscar «un Trove nuevo» sólo
        # serviría para adjudicarse el de otra cuenta y enseñarlo con el nombre
        # cambiado. Antes de rendirse, eso sí, se mira: el juego pudo arrancar y
        # tardar más de la cuenta en recoger el ticket.
        code = result.get("exit_code")
        if not result["consumed"] and code:
            fresh = [pid for pid, _ppid, name in inject.list_processes()
                     if name.lower() == exe.name.lower() and pid not in before]
            if not fresh:
                raise HostUnavailable(
                    f"Trove's anti-cheat loader exited with code {code} without "
                    f"starting the game. Launching several accounts at once is "
                    f"the usual cause; try this one again.")

        return inject.resolve_game_pid(result["pid"], exe.name, exclude=before, log=log)

    def wait_until_ready(self, pid: int, timeout: float = 120.0, log=print) -> bool:
        """Espera a que el juego termine de arrancar. False si se quedó por el camino.

        ``WaitForInputIdle`` es exactamente esta pregunta: vuelve cuando el
        proceso ha acabado su inicialización y está esperando entrada. No mira,
        mueve ni toca ninguna ventana —sólo espera— y es lo que separa «el
        proceso existe» de «el juego ya está en pie».
        """
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u = ctypes.WinDLL("user32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_INFORMATION = 0x0400
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        u.WaitForInputIdle.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        u.WaitForInputIdle.restype = wintypes.DWORD

        handle = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            res = u.WaitForInputIdle(handle, int(timeout * 1000))
        finally:
            k.CloseHandle(handle)
        if res == 0x102:       # WAIT_TIMEOUT
            log(f"[inject] pid {pid} is taking longer than {timeout:.0f}s to come up; "
                f"carrying on")
        alive = any(p == int(pid) for p, _ppid, _name in self.list_processes())
        if not alive:
            log(f"[inject] pid {pid} died while it was starting")
        return alive

    def wait_for_exit(self, pid: int) -> int | None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        INFINITE = 0xFFFFFFFF
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

        handle = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                               False, int(pid))
        if not handle:
            return None
        try:
            k.WaitForSingleObject(handle, INFINITE)
            code = wintypes.DWORD()
            k.GetExitCodeProcess(handle, ctypes.byref(code))
            return int(code.value)
        finally:
            k.CloseHandle(handle)

    def terminate(self, pid: int) -> bool:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_TERMINATE = 0x0001
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

        handle = k.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not handle:
            return False
        try:
            return bool(k.TerminateProcess(handle, 0))
        finally:
            k.CloseHandle(handle)

    def pids_by_name(self, name: str) -> set[int]:
        from . import inject

        return inject.pids_by_name(name)

    def list_processes(self) -> list[tuple[int, int, str]]:
        from . import inject

        return inject.list_processes()


# --- Linux (a través de Wine) -----------------------------------------------


class WineHost(GameHost):
    """Todo pasa por el ayudante; aquí sólo se decide con qué Wine y prefijo.

    El ayudante se arranca a la primera partida y se queda vivo: sus handles son
    los que el juego duplica mientras juega. El prefijo se fija con el primer
    lanzamiento, porque un prefijo distinto es, literalmente, otro disco C: y el
    juego no estaría en él.
    """

    kind = "wine"

    def __init__(self, log=print):
        self._log = log
        self._helper = None
        self._lock = threading.Lock()

    # -- configuración --
    def _settings(self, game_exe: Path | None = None) -> tuple[str, str]:
        """(binario de wine, prefijo) para lanzar ese juego.

        El prefijo se resuelve PRIMERO porque es quien decide el runner: dentro
        de un prefijo de Proton, el Wine que toca es el de ese Proton.
        """
        from . import winehost

        data = prefs.load()
        game = game_exe or data.get("game_path") or None
        prefix = winehost.prefix_for(game, data.get("wine_prefix", ""))
        wine = winehost.find_wine(data.get("wine_binary", ""), prefix)
        return wine, prefix

    def check(self) -> None:
        from . import winehost

        wine, _ = self._settings()
        if not wine:
            raise HostUnavailable(
                "Wine was not found. Install it, or point at it in Settings, "
                "to be able to launch the game.")
        if not winehost.helper_path().is_file():
            raise HostUnavailable(
                f"{winehost.HELPER_NAME} is missing. Build it with "
                f"tools/build_helper.sh.")

    def status(self) -> dict:
        """Lo de siempre, más con qué se va a lanzar y qué Proton hay a mano.

        Aparte va ``warning``: el Wine elegido arranca, pero le falta el símbolo
        que el loader del anti-cheat importa, así que el juego no llegaría a
        abrirse. Es un aviso y no un impedimento —quien quiera intentarlo, que lo
        intente— pero se dice ANTES, no después de un diálogo de error a medio
        traducir.
        """
        from . import winehost

        info = super().status()
        wine, prefix = self._settings()
        runners = winehost.find_proton_runners()
        info.update({"binary": wine, "prefix": prefix, "runners": runners})
        if info["ready"] and winehost.missing_loader_symbol(wine):
            info["warning"] = (
                f"This Wine ({wine}) does not have {winehost.LOADER_SYMBOL}, which "
                f"Trove's anti-cheat loader asks for: the loader shows a "
                f"«procedure entry point» error and the game never starts. "
                + ("Pick one of the Proton runners below — they ship it."
                   if runners else
                   "Install Proton from Steam (any recent version ships it) and "
                   "point Wine binary at its …/files/bin/wine."))
        else:
            info["warning"] = ""
        return info

    def _connect(self, game_exe: Path | None = None):
        from . import winehost

        with self._lock:
            if self._helper is not None and self._helper.alive:
                return self._helper
            self.check()
            wine, prefix = self._settings(game_exe)

            # Un intento y un reintento. Wine falla de vez en cuando al arrancar
            # sobre un prefijo que se está inicializando o cuyo wineserver
            # anterior aún se está apagando; a la segunda va. Si vuelve a fallar
            # es que el problema es de verdad, y el error ya trae lo que dijo
            # Wine (ver WineHelper._explain).
            last = None
            for attempt in (1, 2):
                helper = winehost.WineHelper(wine=wine, prefix=prefix, log=self._log)
                try:
                    helper.start()
                except winehost.WineError as exc:
                    helper.stop()
                    last = exc
                    if attempt == 1:
                        self._log("[wine] the helper did not start; retrying once")
                        time.sleep(2.0)
                    continue
                self._log(f"[wine] helper running (prefix {prefix})")
                self._helper = helper
                return helper
            raise HostUnavailable(str(last))

    # -- operaciones --
    def spawn(self, exe: Path, ticket: str, auth_server: str, *,
              parent_process_name: str = "", exclude: set[int] | None = None,
              log=print) -> int:
        from .winehost import WineError

        helper = self._connect(exe)
        try:
            res = helper.spawn(exe, ticket, auth_server,
                               parent_process_name=parent_process_name,
                               exclude=exclude)
        except WineError as exc:
            raise HostUnavailable(str(exc)) from exc
        if not res["consumed"]:
            log("[wine] the game has not confirmed the ticket yet; "
                "if it sits at the login screen, try again")
        return int(res["pid"])

    def wait_for_exit(self, pid: int) -> int | None:
        from .winehost import WineError

        try:
            return self._connect().wait_for_exit(pid)
        except (WineError, HostUnavailable):
            return None

    def terminate(self, pid: int) -> bool:
        from .winehost import WineError

        try:
            return self._connect().terminate(pid)
        except (WineError, HostUnavailable):
            return False

    def pids_by_name(self, name: str) -> set[int]:
        from .winehost import WineError

        try:
            return self._connect().pids_by_name(name)
        except (WineError, HostUnavailable):
            return set()

    def list_processes(self) -> list[tuple[int, int, str]]:
        from .winehost import WineError

        try:
            return self._connect().list_processes()
        except (WineError, HostUnavailable):
            return []

    def wait_until_ready(self, pid: int, timeout: float = 120.0, log=print) -> bool:
        from .winehost import WineError

        try:
            helper = self._connect()
        except HostUnavailable:
            return False
        try:
            res = helper.wait_until_ready(pid, timeout)
        except WineError as exc:
            log(f"[wine] could not wait for pid {pid}: {exc}")
            return True
        if res == 0x102:
            log(f"[wine] pid {pid} is taking longer than {timeout:.0f}s to come up; "
                f"carrying on")
        return any(p == int(pid) for p, _ppid, _name in self.list_processes())

    def close(self) -> None:
        with self._lock:
            if self._helper is not None:
                self._helper.stop()
                self._helper = None


# --- elección ---------------------------------------------------------------

_host: GameHost | None = None
_host_lock = threading.Lock()


def host(log=print) -> GameHost:
    """El anfitrión de este equipo. Uno solo, y para toda la sesión."""
    global _host
    with _host_lock:
        if _host is None:
            _host = NativeHost() if sys.platform == "win32" else WineHost(log=log)
        return _host
