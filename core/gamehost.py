"""Launching and watching the game, here or inside Wine.

The rest of the application should not need to know which system it runs on.
This module offers the four things a game session needs - start it, know when it
ends, close it and look at what processes exist - and underneath there are two
implementations:

``NativeHost``
    Windows. Calls ``inject.py`` directly: the application IS the launcher the
    game asks for its ticket.

``WineHost``
    Linux. The launcher cannot be a Linux process (see ``winehost.py``), so this
    acts as a go-between with the helper running inside the prefix.

Both speak in "pids" and both return the same shapes. The difference is whose
pid it is: on Windows it is a system pid, on Linux a Wine one. Since every
operation on it goes through the same host that handed it out, that difference
never escapes this file.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from . import prefs


class HostUnavailable(RuntimeError):
    """The game cannot be launched here, with a reason worth showing."""


class GameHost:
    kind = "none"

    def check(self) -> None:
        """Raises ``HostUnavailable`` if playing is not possible right now."""
        raise HostUnavailable("this system cannot launch Trove")

    def spawn(self, exe: Path, ticket: str, auth_server: str, *,
              parent_process_name: str = "", exclude: set[int] | None = None,
              log=print) -> int:
        """Launches and returns the GAME's pid, not that of what was executed.

        ``exclude`` are the sessions the application already watches: with the
        anti-cheat loader in the way the game's process has to be hunted down,
        and without this list two accounts launched at once can end up pointing
        at the same one.
        """
        raise HostUnavailable("this system cannot launch Trove")

    def wait_for_exit(self, pid: int) -> int | None:
        return None

    def terminate(self, pid: int) -> bool:
        return False

    def list_processes(self) -> list[tuple[int, int, str]]:
        """``(pid, parent pid, name)`` of what runs on this host."""
        return []

    def wait_until_ready(self, pid: int, timeout: float = 120.0, log=print) -> bool:
        """Waits for the session to finish starting. False if it died.

        Launching the next account while the previous one is still coming up is
        what makes the anti-cheat loader fall over without launching anything,
        so the caller waits here.
        """
        return True

    def status(self) -> dict:
        """For the interface: whether playing is possible and, if not, why.

        ``warning`` is for what can be launched but will probably go wrong, which
        is not the same as being unable to: see ``WineHost.status``.
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

        # What was already running under that name, plus what we already watch.
        before = inject.pids_by_name(exe.name) | set(exclude or ())
        result = inject.spawn(exe, ticket, auth_server,
                              parent_process_name=parent_process_name or None,
                              log=log)

        # The anti-cheat loader sometimes dies without launching anything at
        # all (exit code 1021, for one). If nobody picked up the ticket either,
        # this launch left no session behind: hunting for "a new Trove" would
        # only claim another account's and show it under the wrong name. Before
        # giving up, though, we do look: the game may have started and simply
        # taken longer than usual to collect the ticket.
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
        """Waits for the game to finish starting. False if it died on the way.

        ``WaitForInputIdle`` asks exactly this question: it returns once the
        process has finished initialising and is waiting for input. It does not
        look at, move or touch any window - it only waits - and it is what
        separates "the process exists" from "the game is up".
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

    def list_processes(self) -> list[tuple[int, int, str]]:
        from . import inject

        return inject.list_processes()


# --- Linux (through Wine) -----------------------------------------------


class WineHost(GameHost):
    """Everything goes through the helper; here only the Wine and prefix are
    decided.

    The helper starts with the first session and stays alive: its handles are the
    ones the game duplicates while playing. The prefix is fixed by the first
    launch, because a different prefix is, literally, a different C: drive and
    the game would not be on it.
    """

    kind = "wine"

    def __init__(self, log=print):
        self._log = log
        self._helper = None
        self._lock = threading.Lock()

    # -- configuration --
    def _settings(self, game_exe: Path | None = None) -> tuple[str, str]:
        """(wine binary, prefix) to launch that game with.

        The prefix is resolved FIRST because it is what decides the runner:
        inside a Proton prefix, the right Wine is that Proton's own.
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
        """The usual, plus what it will launch with and which Protons are around.

        Separately there is ``warning``: the chosen Wine does start, but it lacks
        the symbol the anti-cheat loader imports, so the game would never open.
        It is a warning and not a blocker - anyone who wants to try, may - but it
        is said BEFORE, not after a half-translated error dialog.
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
                f"'procedure entry point' error and the game never starts. "
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

            # One try and one retry. Wine occasionally fails to start against a
            # prefix that is initialising or whose previous wineserver is still
            # shutting down; the second attempt works. If it fails again the
            # problem is real, and the error already carries what Wine said (see
            # WineHelper._explain).
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

    # -- operations --
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


# --- picking one ---------------------------------------------------------------

_host: GameHost | None = None
_host_lock = threading.Lock()


def host(log=print) -> GameHost:
    """This machine's host. Just the one, for the whole session."""
    global _host
    with _host_lock:
        if _host is None:
            _host = NativeHost() if sys.platform == "win32" else WineHost(log=log)
        return _host
