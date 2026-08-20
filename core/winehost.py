"""Hablar con el ayudante Win32 que vive dentro del prefijo de Wine.

En Linux el juego corre bajo Wine, y el ticket sólo se le puede entregar desde
dentro de ese mismo prefijo: es un file-mapping de Windows que el juego duplica
del proceso que lo lanzó (ver ``native/troveinject.c``). Este módulo es el otro
extremo del cable — arranca ``troveinject.exe`` con ``wine``, le manda órdenes y
reparte las respuestas.

Un solo ayudante por sesión, no uno por partida: los handles del ticket tienen
que seguir abiertos mientras haya juego abierto, así que el ayudante hace de
"proceso lanzador" para todas las cuentas, igual que en Windows lo hace la
propia aplicación.

El protocolo es de una línea por mensaje (ver el .c). Aquí lo interesante es que
las respuestas llegan desordenadas: ``wait`` puede tardar horas en contestar y
mientras tanto pasan otros ``spawn`` y ``list``. Por eso cada petición lleva un
número y espera en su propio evento, y hay un hilo lector que reparte.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .paths import base_dir

HELPER_NAME = "troveinject.exe"


class WineError(RuntimeError):
    pass


def helper_path() -> Path:
    """El ejecutable del ayudante, que viaja junto al código."""
    return base_dir() / "native" / HELPER_NAME


def find_wine(preferred: str = "") -> str:
    """Binario de Wine a usar. Vacío si no hay ninguno.

    Se admite que el usuario apunte al suyo (el de Proton, por ejemplo, que vive
    en ``…/dist/bin/wine``); si no, el del sistema.
    """
    for candidate in (preferred, os.environ.get("WINE", ""), "wine", "wine64"):
        if not candidate:
            continue
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else "")
        if found:
            return str(found)
    return ""


def prefix_for(game_path: str | os.PathLike | None, preferred: str = "") -> str:
    """Prefijo de Wine donde vive ese juego.

    Se prefiere lo que diga el usuario. Si no, se deduce de la propia ruta del
    juego: una instalación de Proton cuelga de ``…/compatdata/<appid>/pfx/``, y
    cualquier instalación bajo Wine cuelga de ``<prefijo>/drive_c/``. Deducirlo
    importa porque el juego sólo existe DENTRO de un prefijo: lanzarlo desde
    otro distinto no encontraría ni el disco donde está.
    """
    if preferred:
        return str(preferred)
    if game_path:
        parts = Path(game_path).resolve().parts
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "drive_c":
                return str(Path(*parts[:i]))
    env = os.environ.get("WINEPREFIX", "")
    if env:
        return env
    return str(Path.home() / ".wine")


class WineHelper:
    """El ayudante, visto desde Linux."""

    def __init__(self, *, wine: str, prefix: str, helper: Path | None = None,
                 log=print):
        self.wine = wine
        self.prefix = prefix
        self.helper = Path(helper) if helper else helper_path()
        self._log = log
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, dict] = {}
        self._reader: threading.Thread | None = None
        self._path_cache: dict[str, str] = {}

    # --- ciclo de vida ----------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def env(self) -> dict:
        env = dict(os.environ)
        env["WINEPREFIX"] = self.prefix
        # El ayudante es de consola y no pinta nada; sin esto Wine se queja en
        # equipos sin pantalla y ensucia el registro con avisos inútiles.
        env.setdefault("WINEDEBUG", "-all")
        return env

    def start(self) -> None:
        with self._lock:
            if self.alive:
                return
            if not self.wine:
                raise WineError("no se ha encontrado Wine. Instálalo o indica su "
                                "ruta en Ajustes.")
            if not self.helper.is_file():
                raise WineError(f"falta el ayudante {self.helper}. Compílalo con "
                                f"tools/build_helper.sh.")
            try:
                self._proc = subprocess.Popen(
                    [self.wine, str(self.helper)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=self.env(), bufsize=1,
                    text=True, encoding="utf-8", errors="replace")
            except OSError as exc:
                raise WineError(f"no se pudo arrancar Wine: {exc}") from exc
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="wine-helper")
            self._reader.start()
        # Que responda antes de dar por bueno el arranque: un prefijo roto falla
        # aquí y no a mitad de un lanzamiento.
        self.call("ping", timeout=60)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write("0 quit\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
        # Nadie va a contestar ya: se despierta a quien estuviera esperando.
        with self._lock:
            for slot in self._pending.values():
                slot["error"] = "el ayudante de Wine se ha cerrado"
                slot["event"].set()
            self._pending.clear()

    # --- transporte -------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            head, _, rest = line.partition(" ")
            if head == "log":
                self._log(_decode(rest) or rest)
                continue
            try:
                msg_id = int(head)
            except ValueError:
                self._log(f"[wine] línea inesperada: {line[:120]}")
                continue
            status, _, payload = rest.partition(" ")
            with self._lock:
                slot = self._pending.pop(msg_id, None)
            if not slot:
                continue
            if status == "ok":
                slot["result"] = payload.split(" ") if payload else []
            else:
                slot["error"] = _decode(payload) or "error desconocido"
            slot["event"].set()
        # stdout cerrado: el ayudante murió.
        with self._lock:
            for slot in self._pending.values():
                slot["error"] = "el ayudante de Wine ha muerto"
                slot["event"].set()
            self._pending.clear()

    def call(self, cmd: str, *args, timeout: float | None = 120) -> list[str]:
        """Manda una orden y espera SU respuesta.

        ``timeout=None`` para las que tardan lo que tarden (``wait``). Bloquear
        aquí no bloquea a nadie más: el ayudante atiende cada espera en su propio
        hilo y el lector reparte por número de petición.
        """
        proc = self._proc
        if not proc or proc.poll() is not None:
            raise WineError("el ayudante de Wine no está corriendo")
        slot = {"event": threading.Event(), "result": None, "error": None}
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            self._pending[msg_id] = slot
        line = " ".join([str(msg_id), cmd, *(str(a) for a in args)])
        try:
            assert proc.stdin
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except OSError as exc:
            with self._lock:
                self._pending.pop(msg_id, None)
            raise WineError(f"no se pudo hablar con el ayudante: {exc}") from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise WineError(f"el ayudante no respondió a «{cmd}»")
        if slot["error"]:
            raise WineError(slot["error"])
        return slot["result"] or []

    # --- rutas ------------------------------------------------------------

    def to_windows_path(self, path: str | os.PathLike) -> str:
        """Ruta Linux -> ruta que el ayudante pueda abrir dentro del prefijo."""
        key = str(path)
        cached = self._path_cache.get(key)
        if cached:
            return cached
        try:
            out = subprocess.run([self.wine, "winepath", "-w", key],
                                 capture_output=True, text=True, env=self.env(),
                                 timeout=60)
            win = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            win = ""
        if not win:
            # Reserva: Wine mapea la raíz del sistema en Z:. Sirve para que un
            # fallo de winepath no impida lanzar.
            win = "Z:" + key.replace("/", "\\")
        self._path_cache[key] = win
        return win

    # --- órdenes ----------------------------------------------------------

    def spawn(self, exe: str | os.PathLike, ticket: str, auth_server: str, *,
              parent_process_name: str = "", wait_ms: int = 30000) -> dict:
        res = self.call("spawn",
                        _encode(self.to_windows_path(exe)),
                        _encode(ticket),
                        _encode(auth_server),
                        _encode(parent_process_name or ""),
                        wait_ms,
                        timeout=max(120.0, wait_ms / 1000 + 90))
        if not res:
            raise WineError("el ayudante no devolvió el pid del juego")
        return {"pid": int(res[0]),
                "consumed": len(res) > 1 and res[1] == "1",
                "via_loader": len(res) > 2 and res[2] == "1"}

    def wait_for_exit(self, pid: int) -> int | None:
        res = self.call("wait", int(pid), timeout=None)
        if not res:
            return None
        code = int(res[0])
        return None if code == -1 else code

    def terminate(self, pid: int) -> bool:
        try:
            self.call("kill", int(pid), timeout=30)
            return True
        except WineError:
            return False

    def list_processes(self) -> list[tuple[int, int, str]]:
        out = []
        for item in self.call("list", timeout=60):
            pid, _, rest = item.partition(",")
            ppid, _, name64 = rest.partition(",")
            try:
                out.append((int(pid), int(ppid), _decode(name64) or ""))
            except ValueError:
                continue
        return out

    def pids_by_name(self, name: str) -> set[int]:
        lowered = name.lower()
        return {pid for pid, _ppid, exe in self.list_processes()
                if exe.lower() == lowered}


def _encode(text: str) -> str:
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii") or "="


def _decode(text: str) -> str:
    try:
        return base64.b64decode(text).decode("utf-8", "replace")
    except Exception:
        return ""
