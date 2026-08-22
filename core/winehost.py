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
import collections
import functools
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .paths import base_dir

HELPER_NAME = "troveinject.exe"


class WineError(RuntimeError):
    pass


def helper_path() -> Path:
    """El ejecutable del ayudante, que viaja junto al código."""
    return base_dir() / "native" / HELPER_NAME


# El símbolo que el loader del anti-cheat de Trove importa de ws2_32 y que Wine
# no siempre exporta. Cuando falta, el loader ni siquiera arranca: sale un
# «The procedure entry point ... could not be located» y el juego no aparece.
# Proton sí lo trae, y por eso se prefiere (ver find_wine).
LOADER_SYMBOL = "WSCEnumProtocols32"


def _executable(candidate: str) -> str:
    """La ruta de un binario ejecutable, buscándolo en el PATH si hace falta."""
    if not candidate:
        return ""
    found = shutil.which(candidate)
    if found:
        return str(found)
    path = Path(candidate)
    return str(path) if path.is_file() and os.access(path, os.X_OK) else ""


def _version_key(name: str) -> tuple:
    """Para ordenar Proton por versión: los números del nombre, de mayor a menor."""
    return tuple(int(n) for n in re.findall(r"\d+", name)) or (0,)


# La búsqueda de runners toca disco y la interfaz pregunta el estado a menudo:
# se recuerda un rato. Un Proton recién instalado tarda eso en aparecer, que para
# algo que se instala una vez al año es un precio razonable.
_RUNNER_TTL = 60.0
_runner_cache: tuple[float, list[dict]] = (0.0, [])


def find_proton_runners(fresh: bool = False) -> list[dict]:
    """Los Proton instalados en este equipo, del más nuevo al más viejo.

    Cada uno es ``{"name", "wine"}``. Se miran los dos sitios donde acaban: los
    Proton oficiales, dentro de las bibliotecas de Steam, y los de la comunidad
    (GE-Proton y demás), en ``compatibilitytools.d``. El binario está en
    ``files/bin/wine`` desde Proton 5.13 y en ``dist/bin/wine`` en los anteriores.
    """
    global _runner_cache
    from . import installs

    stamp, cached = _runner_cache
    if not fresh and time.monotonic() - stamp < _RUNNER_TTL:
        return cached

    roots: list[Path] = []
    for steam in installs._steam_roots():
        for library in [steam] + installs._steam_library_paths(steam):
            roots.append(library / "steamapps" / "common")
        roots.append(steam / "compatibilitytools.d")
    roots.append(Path.home() / ".steam" / "root" / "compatibilitytools.d")

    found: dict[str, dict] = {}
    for root in roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if root.name == "common" and not child.name.lower().startswith("proton"):
                continue
            wine = _proton_wine(child)
            if wine and wine not in found:
                found[wine] = {"name": child.name, "wine": wine}
    runners = sorted(found.values(),
                     key=lambda r: _version_key(r["name"]), reverse=True)
    _runner_cache = (time.monotonic(), runners)
    return runners


def _proton_wine(install: Path) -> str:
    """El binario de wine dentro de una carpeta de Proton, si lo hay."""
    for relative in ("files/bin/wine", "dist/bin/wine",
                     "files/bin/wine64", "dist/bin/wine64"):
        candidate = install / relative
        if candidate.is_file():
            return str(candidate)
    return ""


def proton_for_prefix(prefix: str | os.PathLike) -> str:
    """El Proton que hizo ese prefijo, si el prefijo lo dice.

    Un prefijo de Proton no es un prefijo de Wine cualquiera: lo creó un runner
    concreto, y Steam deja constancia de cuál en ``config_info`` (rutas dentro
    del propio Proton) y en ``version`` (su nombre). Usar ese mismo runner evita
    la mitad de los problemas —empezando por el «wineserver version mismatch»— y
    trae de vuelta lo que el Wine del sistema no tiene.
    """
    compat = Path(prefix)
    if compat.name == "pfx":
        compat = compat.parent
    if compat.parent.name != "compatdata":
        return ""

    try:
        info = (compat / "config_info").read_text(encoding="utf-8", errors="replace")
    except OSError:
        info = ""
    for line in info.splitlines():
        for marker in ("/files/", "/dist/"):
            head, sep, _rest = line.partition(marker)
            if sep and head:
                wine = _proton_wine(Path(head))
                if wine:
                    return wine

    try:
        version = (compat / "version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    wanted = version.split()[-1].lower() if version.split() else ""
    if not wanted:
        return ""
    for runner in find_proton_runners():
        name = runner["name"].lower()
        if wanted in name or name.replace(" ", "-") in wanted:
            return runner["wine"]
    return ""


def find_wine(preferred: str = "", prefix: str = "") -> str:
    """Binario de Wine a usar para ese prefijo. Vacío si no hay ninguno.

    Manda lo que diga el usuario. Después, el runner del propio prefijo: si el
    juego vive dentro de un prefijo de Proton, el Wine que le corresponde es ese
    Proton y no el del sistema —que además de no haber hecho el prefijo, es el
    que se queda sin ``WSCEnumProtocols32`` y deja al anti-cheat sin arrancar.
    Y si no hay ni una cosa ni la otra, el del sistema; y si tampoco, cualquier
    Proton que haya por aquí, que es mejor que nada.
    """
    for candidate in (preferred, os.environ.get("WINE", "")):
        found = _executable(candidate)
        if found:
            return found
    if prefix:
        proton = proton_for_prefix(prefix)
        if proton:
            return proton
    for candidate in ("wine", "wine64"):
        found = _executable(candidate)
        if found:
            return found
    runners = find_proton_runners()
    return runners[0]["wine"] if runners else ""


@functools.lru_cache(maxsize=8)
def missing_loader_symbol(wine: str) -> bool:
    """¿Le falta a este Wine lo que el anti-cheat de Trove le va a pedir?

    ``ws2_32.dll`` es una DLL de Wine, no del prefijo, así que la respuesta está
    en el propio runner. Se busca el nombre del símbolo dentro del archivo: en un
    PE los nombres exportados están ahí en texto plano, y para lo que hace falta
    aquí —avisar antes de que el usuario se coma un diálogo de error en chino—
    eso basta. Ante la duda (no encontramos la DLL), se calla.
    """
    dll = _ws2_32_of(wine)
    if not dll:
        return False
    try:
        return LOADER_SYMBOL.encode("ascii") not in dll.read_bytes()
    except OSError:
        return False


def _ws2_32_of(wine: str) -> Path | None:
    """El ws2_32.dll que usaría ese binario de wine."""
    if not wine:
        return None
    root = Path(wine).resolve().parent.parent      # …/bin/wine -> …
    for pattern in ("lib/wine/x86_64-windows/ws2_32.dll",
                    "lib64/wine/x86_64-windows/ws2_32.dll",
                    "lib/*/wine/x86_64-windows/ws2_32.dll"):
        for candidate in root.glob(pattern):
            if candidate.is_file():
                return candidate
    # Wine del sistema: el binario está en /usr/bin y las DLL cuelgan de la
    # carpeta de arquitectura, que varía según la distribución.
    for candidate in root.glob("lib*/*/wine/x86_64-windows/ws2_32.dll"):
        if candidate.is_file():
            return candidate
    return None


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
        # Lo último que Wine haya dicho por stderr. Cuando el ayudante se muere,
        # la razón está AHÍ y en ningún otro sitio: "wineserver: version
        # mismatch", "wine: could not load...", un prefijo de otro runner. Sin
        # esto sólo quedaba un "el ayudante ha muerto" que no ayuda a nadie.
        self._stderr: collections.deque[str] = collections.deque(maxlen=25)
        self._errthread: threading.Thread | None = None

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
                raise WineError("Wine was not found. Install it, or point at it "
                                "in Settings.")
            if not self.helper.is_file():
                raise WineError(f"the helper {self.helper} is missing. Build it with "
                                f"tools/build_helper.sh.")
            self._stderr.clear()
            try:
                self._proc = subprocess.Popen(
                    [self.wine, str(self.helper)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=self.env(), bufsize=1,
                    text=True, encoding="utf-8", errors="replace")
            except OSError as exc:
                raise WineError(f"could not start Wine: {exc}") from exc
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="wine-helper")
            self._reader.start()
            self._errthread = threading.Thread(target=self._drain_stderr,
                                               daemon=True, name="wine-helper-err")
            self._errthread.start()
        # Que responda antes de dar por bueno el arranque: un prefijo roto falla
        # aquí y no a mitad de un lanzamiento.
        try:
            self.call("ping", timeout=60)
        except WineError as exc:
            raise WineError(self._explain(str(exc))) from exc

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
                slot["error"] = "the Wine helper was shut down"
                slot["event"].set()
            self._pending.clear()

    # --- transporte -------------------------------------------------------

    def _drain_stderr(self) -> None:
        """Guarda lo que Wine escupe por stderr, sin volcarlo al registro.

        Wine es hablador incluso cuando todo va bien, así que esto no se enseña
        salvo que algo falle; entonces es lo único que explica el fallo.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for raw in proc.stderr:
            text = raw.rstrip()
            if text:
                self._stderr.append(text)

    def _explain(self, problem: str) -> str:
        """El error, con lo que Wine dijo y una pista si se puede afinar.

        Idempotente: el lector explica en cuanto el proceso muere, y el arranque
        vuelve a pasar por aquí con ese mismo texto. Sin esta guarda, el usuario
        leía la queja de Wine dos veces seguidas.
        """
        if "wine said:" in problem or "wine exited with code" in problem:
            return problem
        # Esperar al hilo que lee stderr: sin esto se explicaba el fallo ANTES
        # de que Wine terminara de contarlo, y salía un "ha muerto" pelado.
        if self._errthread and self._errthread.is_alive():
            self._errthread.join(timeout=5)
        parts = [problem]
        proc = self._proc
        code = proc.poll() if proc else None
        if code is not None:
            parts.append(f"wine exited with code {code}")
        tail = [t for t in self._stderr][-6:]
        if tail:
            parts.append("wine said:\n  " + "\n  ".join(tail))
        hint = self._hint(" ".join(self._stderr).lower())
        if hint:
            parts.append(hint)
        return " — ".join(parts[:2]) + ("\n" + "\n".join(parts[2:]) if parts[2:] else "")

    def _hint(self, said: str) -> str:
        """Pistas para los fallos que tienen un nombre conocido."""
        if "version mismatch" in said or "wineserver" in said:
            return ("Hint: a wineserver from a DIFFERENT Wine build is already "
                    "running for this prefix. Close the app that opened it "
                    "(Bottles, Lutris, Steam) or run "
                    f"`WINEPREFIX={self.prefix} wineserver -k`, then try again.")
        if "/bottles/" in str(self.prefix).lower():
            return ("Hint: this prefix was made by Bottles, which uses its own "
                    "Wine build. Point Settings → Wine at the same runner "
                    "Bottles uses for this bottle, or launch it from Bottles "
                    "once so its runner updates the prefix.")
        if "compatdata" in str(self.prefix).lower():
            return ("Hint: this is a Proton prefix. Point Settings → Wine at "
                    "that Proton's own binary (…/dist/bin/wine or "
                    "…/files/bin/wine).")
        return ""

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
                self._log(f"[wine] unexpected line: {line[:120]}")
                continue
            status, _, payload = rest.partition(" ")
            with self._lock:
                slot = self._pending.pop(msg_id, None)
            if not slot:
                continue
            if status == "ok":
                slot["result"] = payload.split(" ") if payload else []
            else:
                slot["error"] = _decode(payload) or "unknown error"
            slot["event"].set()
        # stdout cerrado: el ayudante murió. Se le da un instante a Wine para
        # terminar de escribir su queja antes de contarla.
        if self._proc:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        message = self._explain("the Wine helper died")
        with self._lock:
            for slot in self._pending.values():
                slot["error"] = message
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
            raise WineError("the Wine helper is not running")
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
            raise WineError(f"could not talk to the helper: {exc}") from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise WineError(f"the helper did not answer «{cmd}»")
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
              parent_process_name: str = "", wait_ms: int = 30000,
              exclude: set[int] | None = None) -> dict:
        """``exclude``: partidas que ya vigilamos, para que el ayudante no las
        confunda con la que acaba de abrir (ver resolve_game_pid en el .c)."""
        res = self.call("spawn",
                        _encode(self.to_windows_path(exe)),
                        _encode(ticket),
                        _encode(auth_server),
                        _encode(parent_process_name or ""),
                        wait_ms,
                        ",".join(str(int(p)) for p in sorted(exclude or [])) or "0",
                        timeout=max(120.0, wait_ms / 1000 + 90))
        if not res:
            raise WineError("the helper did not return the game pid")
        return {"pid": int(res[0]),
                "consumed": len(res) > 1 and res[1] == "1",
                "via_loader": len(res) > 2 and res[2] == "1"}

    def wait_for_exit(self, pid: int) -> int | None:
        res = self.call("wait", int(pid), timeout=None)
        if not res:
            return None
        code = int(res[0])
        return None if code == -1 else code

    def wait_until_ready(self, pid: int, timeout: float = 120.0) -> int:
        """Espera a que esa partida acabe de arrancar, DENTRO del prefijo.

        Devuelve lo que devuelva ``WaitForInputIdle`` allí: 0 si ya está en pie,
        0x102 si se acabó el tiempo, y -1 si el proceso ni siquiera se pudo
        abrir. Como ``wait``, se atiende en un hilo del ayudante, así que
        esperar aquí no deja al resto sin línea.
        """
        res = self.call("ready", int(pid), int(timeout * 1000),
                        timeout=timeout + 30)
        return int(res[0]) if res else -1

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
