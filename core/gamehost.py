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

    def close(self) -> None:
        pass

    def status(self) -> dict:
        """Para la interfaz: si se puede jugar y, si no, por qué."""
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
        spawn_pid = inject.spawn(exe, ticket, auth_server,
                                 parent_process_name=parent_process_name or None,
                                 log=log)
        return inject.resolve_game_pid(spawn_pid, exe.name, exclude=before, log=log)

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
    def _settings(self) -> tuple[str, str]:
        from . import winehost

        data = prefs.load()
        wine = winehost.find_wine(data.get("wine_binary", ""))
        prefix = data.get("wine_prefix", "")
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

    def _connect(self, game_exe: Path | None = None):
        from . import winehost

        with self._lock:
            if self._helper is not None and self._helper.alive:
                return self._helper
            self.check()
            wine, configured = self._settings()
            prefix = winehost.prefix_for(game_exe, configured)
            helper = winehost.WineHelper(wine=wine, prefix=prefix, log=self._log)
            helper.start()
            self._log(f"[wine] helper running (prefix {prefix})")
            self._helper = helper
            return helper

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
