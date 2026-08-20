"""Orquestador del launcher: lo que la interfaz llama de verdad.

Modelo de lanzamiento
---------------------

Cada **cuenta** lleva su propia región (NA/EU/PTS) y se lanza desde su fila, sin
que exista una «cuenta activa» global. La región determina dos cosas:

  * el servidor de autenticación al que apunta el juego, y
  * qué instalación se usa: NA y EU comparten los archivos de Live, mientras que
    PTS necesita la carpeta de PTS. Por eso ``_resolve_game_dir`` elige carpeta
    según la región y no según un selector global.

Concurrencia
------------

Las operaciones pesadas (comprobar, actualizar, reparar) corren en un hilo
demonio y sólo una a la vez, protegida por ``_busy``. Los **lanzamientos** son la
excepción: se preparan varios a la vez, porque lanzar un grupo entero es
justamente el caso de uso. Cada lanzamiento lleva su propio hilo y su propia cola
de 2FA, identificada por el email.

Lo que NO va en paralelo son dos cosas, y las dos por el mismo motivo —que se
pisan entre ellas sobre un recurso compartido:

  * la fase de actualización, una por carpeta de juego (``_sync_for_play``), y
  * el arranque en sí (``_spawn_game``), de uno en uno y con un respiro entre
    cuentas, porque el proceso del juego se identifica por «el que no estaba
    antes» y dos arranques simultáneos se adjudican el mismo.

Auto-relog
----------

Tras lanzar, un hilo vigila el PID. Cuando el juego termina —se haya caído o se
haya cerrado con normalidad, que es lo que pasa al echarte por inactividad—
volvemos a autenticar y relanzar. No se relanza lo que cerró el usuario desde el
launcher, ni lo que se muere antes de ``_MIN_UPTIME_FOR_RELOG``, para no
encadenar arranques fallidos. Y si la caída dejó abierto el reportador de fallos
de Trove, se cierra: ver ``_close_crash_handler``.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from . import gamehost, installs, prefs, trionauth
from . import vault as vault_mod
from . import updater as _updater
from .paths import app_data_dir, macaddr_path, trove_appdata_dir

# --- constantes -------------------------------------------------------------

# CDN de actualizaciones de Trion (HTTP plano, sin autenticación). El doble
# slash tras el prefijo es deliberado: replica literalmente lo que hace Glyph.
UPDATE_BASE = "http://trove-update.dyn.triongames.com"
UPDATE_PREFIX = "/kiwi-live-client-patch/"

GLYPH_USER_AGENT = "Glyph (stable-248-1-a-336302)"
GLYPH_CHANNEL = "131"
KEY_FILE = "Trove_x64.exe"

# Proceso bajo el que reparentar el lanzamiento cuando la opción está activa.
# Ver core/inject.py: sólo cambia la ancestría del proceso, y únicamente si Glyph
# ya está corriendo. Desactivado por defecto.
REPARENT_PROCESS = "GlyphClientApp.exe"

# región -> (rama del CDN, tipo de instalación que necesita)
REGION_BRANCH = {"NA": ("live-us", "live"), "EU": ("live-us", "live"),
                 "PTS": ("pts", "pts")}

_CANCEL_2FA = object()          # centinela que aborta un lanzamiento en espera de código
_MIN_UPTIME_FOR_RELOG = 25.0    # segundos: si muere antes, no relanzamos

# Respiro entre arranques consecutivos. No es estética: el proceso del juego se
# identifica por "el que no estaba antes", así que el anterior tiene que haber
# aparecido ya en la lista de procesos cuando el siguiente mire.
_LAUNCH_GAP = 2.5

# Cuánto damos por buena una sincronización recién hecha sobre la misma carpeta.
# Lanzar diez cuentas no son diez comprobaciones de actualización seguidas de la
# misma instalación.
_UPDATE_FRESH_FOR = 120.0

# El reportador de fallos que Trove deja abierto al crashear. No conocemos su
# nombre exacto en todas las instalaciones, así que se busca por esta pista y se
# exige además que sea cosa de ESTA partida (ver _close_crash_handler).
_CRASH_HINT = "crash"


class LauncherService:
    """Toda la lógica del launcher. La UI sólo llama a métodos de esta clase."""

    def __init__(self, emit=None, log=print):
        # ``emit(payload: dict)`` empuja un evento a la interfaz. Si no hay UI
        # conectada todavía, los eventos se descartan sin ruido.
        self._emit_cb = emit
        self._log = log

        self._state_lock = threading.Lock()
        self._busy = False
        self._busy_op: str | None = None

        # Una cola de 2FA por email: varios lanzamientos pueden estar esperando
        # código a la vez, y cada diálogo debe desbloquear el suyo.
        self._2fa: dict[str, queue.Queue] = {}
        self._2fa_lock = threading.Lock()

        self._launch_lock = threading.Lock()
        self._launches: dict[int, dict] = {}
        # email -> 'launching' | 'checking': qué tiene ocupada a cada cuenta, para
        # que su fila lo refleje y no se pueda lanzar y comprobar a la vez.
        self._account_busy: dict[str, str] = {}
        # pids que estamos matando a propósito: su muerte NO debe disparar el
        # auto-relog, porque el usuario pidió explícitamente cerrar el juego.
        self._stopping: set[int] = set()

        # Los lanzamientos se preparan en paralelo pero ARRANCAN de uno en uno:
        # ver _spawn_game. El sello es del último arranque, para dejar el respiro.
        self._spawn_gate = threading.Lock()
        self._last_spawn_at = 0.0

        # Una carpeta de juego, una actualización a la vez: lanzar diez cuentas
        # no puede poner diez updaters a escribir sobre los mismos archivos.
        self._dir_locks: dict[str, threading.Lock] = {}
        self._dir_synced: dict[str, float] = {}
        self._dir_guard = threading.Lock()

        # email -> {"ok": bool, "detail": str}: resultado del último intento real
        # (comprobar o lanzar) EN ESTA SESIÓN. Deliberadamente en memoria y no en
        # disco: al abrir el launcher no sabemos si una cuenta entra hasta
        # probarla, así que arranca en gris en vez de mentir con un "Ready".
        self._last_result: dict[str, dict] = {}

    def set_emitter(self, emit) -> None:
        self._emit_cb = emit

    # --- eventos hacia la UI ------------------------------------------------

    def emit(self, op: str, stage: str, **fields) -> None:
        payload = {"op": op, "stage": stage}
        payload.update(fields)
        if self._emit_cb is None:
            return
        try:
            self._emit_cb(payload)
        except Exception:
            pass  # la UI no está escuchando (ventana cerrándose): nada que hacer

    def _logger(self, op: str, email: str = ""):
        def _log(message) -> None:
            self._log(f"[{op}] {message}")
            self.emit(op, "log", message=str(message), email=email)
        return _log

    def _progress(self, op: str, stage: str = "downloading", email: str = ""):
        """Callback de progreso limitado a un evento cada 150 ms, pero que
        siempre deja pasar el último (si no, la barra se queda a medias)."""
        last = [0.0]

        def _cb(seen: int, total: int, downloaded: int) -> None:
            now = time.monotonic()
            if total and now - last[0] < 0.15 and seen < total:
                return
            last[0] = now
            self.emit(op, stage, current=seen, total=total, downloaded=downloaded,
                      email=email)
        return _cb

    # --- planificación del hilo de trabajo ----------------------------------

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._busy

    def _begin(self, op: str) -> bool:
        with self._state_lock:
            if self._busy:
                return False
            self._busy = True
            self._busy_op = op
            return True

    def _finish(self) -> None:
        with self._state_lock:
            self._busy = False
            self._busy_op = None

    def _spawn(self, op: str, target) -> dict:
        """Ejecuta ``target()`` en un hilo demonio si no hay otra cosa en curso.

        Sólo para mantenimiento: los lanzamientos usan ``_spawn_launch``, que no
        toma el cerrojo porque varias cuentas pueden arrancar a la vez.
        """
        if not self._begin(op):
            return {"started": False, "error": "Another operation is already running.",
                    "busy_with": self._busy_op}

        def _run() -> None:
            try:
                target()
            except Exception as exc:
                self.emit(op, "error", done=True, ok=False, error=str(exc),
                          message=str(exc))
            finally:
                self._finish()

        threading.Thread(target=_run, daemon=True, name=f"trove-{op}").start()
        return {"started": True}

    # --- instalaciones ------------------------------------------------------

    def install_list(self) -> list[dict]:
        """Instalaciones ya conocidas. Si aún no se ha escaneado, devuelve lo que
        haya y lanza el escaneo en segundo plano: la ventana no debe esperar a
        que un disco dormido despierte."""
        custom = prefs.load().get("custom_dirs", [])
        if installs.is_scanned():
            return installs.detect(custom)

        threading.Thread(target=self._scan_installs_async, daemon=True,
                         name="trove-scan").start()
        return []

    def _scan_installs_async(self) -> None:
        try:
            found = installs.detect(prefs.load().get("custom_dirs", []))
        except Exception as exc:
            self.emit("installs", "error", error=str(exc))
            return
        self.emit("installs", "update", installs=found)

    def _resolve_game_dir(self, region: str) -> Path:
        """Carpeta del juego que corresponde a una región.

        NA y EU usan los archivos de Live; PTS necesita su propia carpeta. Si el
        usuario no ha fijado una carpeta de PTS, cogemos la primera instalación
        detectada de ese tipo antes de rendirnos.
        """
        _branch, kind = REGION_BRANCH.get(region, REGION_BRANCH["EU"])
        data = prefs.load()
        raw = data.get("pts_game_path") if kind == "pts" else data.get("game_path")

        if not raw and kind == "pts":
            for game in installs.detect(data.get("custom_dirs", [])):
                if game["kind"] == "pts":
                    raw = game["path"]
                    break

        if not raw:
            if kind == "pts":
                raise ValueError(
                    "No PTS installation set. Add one from the bottom bar or "
                    "change the account's region.")
            raise ValueError("No Trove installation selected.")

        path = Path(raw)
        if not path.is_dir():
            raise ValueError(f"Trove folder not found: {raw}")
        return path

    def _resolve_exe(self, game_dir: Path) -> Path:
        exe = installs.find_executable(game_dir)
        return exe if exe is not None else game_dir / KEY_FILE

    def _make_auth(self, email: str, password: str) -> trionauth.TrionAuth:
        return trionauth.TrionAuth(
            username=email or "", password=password or "",
            channel=GLYPH_CHANNEL, user_agent=GLYPH_USER_AGENT,
            cache_key=prefs.auth_key(email),
            macaddr_path=macaddr_path(),
            log=self._log,
        )

    def _parent_process(self) -> str | None:
        return REPARENT_PROCESS if prefs.load().get("reparent_glyph") else None

    # --- estado para la interfaz --------------------------------------------

    def _account_state(self, email: str, instance: dict | None) -> dict:
        """Estado de una cuenta: ``{"status", "detail"}``.

        Los estados posibles, de más a menos concreto:

        ``running``   el juego está abierto y sabemos con qué pid.
        ``launching`` / ``checking``  hay una operación en vuelo.
        ``failed``    el último intento de esta sesión falló; ``detail`` explica por qué.
        ``ready``     comprobado en esta sesión: la cuenta entra.
        ``pending``   faltan datos (ni contraseña guardada ni ticket en caché).
        ``unknown``   tiene credenciales, pero aún no lo hemos comprobado.

        La diferencia entre ``unknown`` y ``ready`` es a propósito: tener una
        contraseña guardada no prueba que Trion la acepte, así que no pintamos
        verde hasta haberlo verificado de verdad.
        """
        key = email.lower()
        with self._launch_lock:
            busy = self._account_busy.get(key)
        if busy:
            return {"status": busy, "detail": ""}
        if instance:
            return {"status": "running",
                    "detail": f"pid {instance['pid']} · up {instance['uptime']}s"
                              + (f" · {instance['relogs']}x relog" if instance.get("relogs") else "")}

        result = self._last_result.get(key)
        if result and not result.get("ok"):
            return {"status": "failed", "detail": result.get("detail", "")}
        if result and result.get("ok"):
            return {"status": "ready", "detail": result.get("detail", "")}

        if prefs.has_saved_password(email):
            return {"status": "unknown", "detail": "Password saved, not verified yet."}
        try:
            if self._make_auth(email, "").has_valid_cache():
                return {"status": "unknown", "detail": "Cached session, not verified yet."}
        except Exception:
            pass
        return {"status": "pending",
                "detail": "No saved password and no cached session — it will ask when you launch."}

    def _record_result(self, email: str, ok: bool, detail: str = "") -> None:
        self._last_result[email.lower()] = {"ok": ok, "detail": detail}

    def state(self) -> dict:
        data = prefs.load()
        running = self.running_list()
        by_email = {i["email"].lower(): i for i in running}

        accounts = []
        for account in prefs.accounts():
            email = account.get("email", "")
            instance = by_email.get(email.lower())
            accounts.append({
                **account,
                "auto_relog": bool(account.get("auto_relog", False)),
                "flagged": bool(account.get("flagged", False)),
                "label": account.get("name") or prefs.mask_email(email),
                "masked": prefs.mask_email(email),
                **self._account_state(email, instance),
                "pid": instance["pid"] if instance else None,
                "uptime": instance["uptime"] if instance else 0,
                "has_saved_password": prefs.has_saved_password(email),
            })

        return {
            "groups": prefs.groups(),
            "accounts": accounts,
            "installs": self.install_list(),
            "regions": list(prefs.REGIONS),
            "game_path": data.get("game_path", ""),
            "pts_game_path": data.get("pts_game_path", ""),
            "hide_emails": bool(data.get("hide_emails", True)),
            "remember_password": bool(data.get("remember_password", True)),
            "wine_binary": data.get("wine_binary", ""),
            "wine_prefix": data.get("wine_prefix", ""),
            "theme": data.get("theme") or dict(prefs.DEFAULTS["theme"]),
            "update_first": bool(data.get("update_first", True)),
            "reparent_glyph": bool(data.get("reparent_glyph", False)),
            "folders": self._folders(data),
            "versions": self._versions(data),
            # Con qué se lanza aquí y dónde se guardan los secretos: la
            # interfaz lo necesita para explicar por qué algo no se puede.
            "host": self._host().status(),
            "vault": vault_mod.status(),
            "busy": self.busy,
            "busy_op": self._busy_op,
            "running": running,
        }

    def _folders(self, data: dict) -> list[dict]:
        """Rutas que la interfaz muestra en Ajustes -> Folders.

        Sólo lectura: abrirlas sigue siendo cosa de ``open_folder``. Se listan
        aunque no existan todavía para que el usuario vea dónde van a estar.
        """
        pts = data.get("pts_game_path", "")
        if not pts:
            for game in (installs.detect(data.get("custom_dirs", [])) if installs.is_scanned() else []):
                if game["kind"] == "pts":
                    pts = game["path"]
                    break
        out = [
            {"kind": "game", "label": "Trove (Live)", "path": data.get("game_path", "")},
            {"kind": "pts", "label": "Trove (PTS)", "path": pts},
            {"kind": "modcfg", "label": "Mod configs", "path": str(trove_appdata_dir() / "ModCfgs")},
            {"kind": "data", "label": "App data", "path": str(app_data_dir())},
        ]
        return [f for f in out if f["path"]]

    def _versions(self, data: dict) -> dict:
        """Versión aplicada localmente por rama, para la vista de mantenimiento."""
        versions: dict[str, str | None] = {}
        for branch, raw in (("live-us", data.get("game_path")),
                            ("pts", data.get("pts_game_path"))):
            if not raw or not Path(raw).is_dir():
                versions[branch] = None
                continue
            try:
                up = _updater.Updater(base=UPDATE_BASE, prefix=UPDATE_PREFIX,
                                      branch=branch, game_dir=Path(raw),
                                      db_path=prefs.update_db_path(branch, Path(raw)),
                                      log=lambda _m: None)
                try:
                    versions[branch] = up.current_version()
                finally:
                    up.close()
            except Exception:
                versions[branch] = None
        return versions

    # --- comprobar / actualizar / reparar -----------------------------------

    def _run_sync(self, op: str, game_dir: Path, branch: str, *, adopt: bool,
                  reset: bool, emit_done: bool = True, email: str = "") -> dict:
        """Cuerpo común de actualizar (adoptar) y reparar (borrar estado y
        redescargar). Con ``emit_done=False`` no emitimos el evento final: se usa
        cuando esta sincronización es sólo la *fase* de actualización de un
        lanzamiento, para que la UI no crea que el Play entero ha terminado.
        """
        self.emit(op, "starting", email=email,
                  message="Contacting the update server...")
        up = _updater.Updater(base=UPDATE_BASE, prefix=UPDATE_PREFIX, branch=branch,
                              game_dir=game_dir,
                              db_path=prefs.update_db_path(branch, game_dir),
                              log=self._logger(op, email))
        try:
            if reset:
                up.reset()
            result = up.update(key_file=KEY_FILE, adopt=adopt,
                               progress=self._progress(op, email=email))
        finally:
            up.close()

        if emit_done:
            ok = result["failed"] == 0
            if result.get("skipped"):
                message = f"Already on the latest version ({result['version']})."
            elif ok:
                message = (f"Done: {result['version']} — {result['downloaded']} downloaded, "
                           f"{result['unchanged']} unchanged.")
            else:
                message = (f"Finished with {result['failed']} failed files. "
                           f"Run it again to resume.")
            self.emit(op, "done", done=True, ok=ok, message=message, **result)
        return result

    def check(self, target: str = "live") -> dict:
        branch = "pts" if target == "pts" else "live-us"
        game_dir = self._resolve_game_dir("PTS" if target == "pts" else "EU")

        def _work() -> None:
            self.emit("check", "starting", message="Checking for updates...")
            up = _updater.Updater(base=UPDATE_BASE, prefix=UPDATE_PREFIX, branch=branch,
                                  game_dir=game_dir,
                                  db_path=prefs.update_db_path(branch, game_dir),
                                  log=self._logger("check"))
            try:
                info = up.check()
                local = up.current_version()
            finally:
                up.close()
            message = ("You are on the latest version." if info["up_to_date"]
                       else f"An update is available: {info['version']}.")
            self.emit("check", "done", done=True, ok=True, message=message,
                      version=info["version"], local_version=local,
                      up_to_date=info["up_to_date"])

        return self._spawn("check", _work)

    def update(self, target: str = "live") -> dict:
        branch = "pts" if target == "pts" else "live-us"
        game_dir = self._resolve_game_dir("PTS" if target == "pts" else "EU")
        return self._spawn("update", lambda: self._run_sync(
            "update", game_dir, branch, adopt=True, reset=False))

    def repair(self, target: str = "live") -> dict:
        branch = "pts" if target == "pts" else "live-us"
        game_dir = self._resolve_game_dir("PTS" if target == "pts" else "EU")
        return self._spawn("repair", lambda: self._run_sync(
            "repair", game_dir, branch, adopt=False, reset=True))

    # --- 2FA ----------------------------------------------------------------

    def _token_provider(self, email: str, op: str = "play"):
        """Lo que TrionAuth llama cuando el servidor exige un código de 2 pasos:
        avisa a la interfaz y bloquea ESA operación hasta que llega el código."""
        def _provider() -> str:
            pending: queue.Queue = queue.Queue()
            with self._2fa_lock:
                self._2fa[email.lower()] = pending
            self.emit(op, "2fa_required", email=email,
                      label=prefs.display_name(email),
                      message="Enter the verification code sent to your email.")
            code = pending.get()
            with self._2fa_lock:
                self._2fa.pop(email.lower(), None)
            if code is _CANCEL_2FA:
                raise trionauth.AuthError("Cancelled at two-step verification.")
            return str(code).strip()
        return _provider

    # --- ocupación por cuenta ----------------------------------------------

    def _claim_account(self, email: str, what: str) -> str | None:
        """Marca la cuenta como ocupada. Devuelve el motivo si ya lo estaba."""
        with self._launch_lock:
            current = self._account_busy.get(email.lower())
            if current:
                return current
            self._account_busy[email.lower()] = what
        return None

    def _release_account(self, email: str) -> None:
        with self._launch_lock:
            self._account_busy.pop(email.lower(), None)

    def submit_2fa(self, email: str, code: str) -> dict:
        with self._2fa_lock:
            pending = self._2fa.get((email or "").lower())
        if pending is None:
            return {"ok": False, "error": "No operation is waiting for a code."}
        pending.put(str(code or "").strip())
        return {"ok": True}

    def cancel_2fa(self, email: str) -> dict:
        with self._2fa_lock:
            pending = self._2fa.get((email or "").lower())
        if pending is not None:
            pending.put(_CANCEL_2FA)
        return {"ok": True}

    # --- seguimiento de procesos lanzados + auto-relog ----------------------

    def _tracked_pids(self) -> set[int]:
        """Pids de partidas que ya seguimos. Se excluyen al resolver el proceso
        de un lanzamiento nuevo: si dos cuentas arrancan a la vez y una tiene que
        recurrir al último recurso ('cualquier Trove nuevo'), sin esto podría
        adjudicarse la partida de la otra."""
        with self._launch_lock:
            return set(self._launches)

    def running_list(self) -> list[dict]:
        with self._launch_lock:
            items = [dict(i) for i in self._launches.values()]
        now = time.time()
        return [{
            "pid": i["pid"],
            "email": i["email"],
            "label": prefs.display_name(i["email"]),
            "region": i["region"],
            "auto_relog": bool(i.get("auto_relog")),
            "relogs": i.get("relogs", 0),
            "uptime": int(now - i["started_at"]),
        } for i in items]

    def _emit_running(self) -> None:
        self.emit("running", "update", instances=self.running_list())

    def _host(self) -> "gamehost.GameHost":
        """Quien sabe lanzar en este equipo: Windows directo, o Wine."""
        return gamehost.host(log=self._log)

    def _track_launch(self, pid: int, info: dict) -> int:
        """Apunta una partida y ponle un vigilante. Devuelve su pid.

        Si ese pid ya es de otra cuenta, no lo pisamos: significa que la
        resolución se equivocó y quedarnos con el último dejaría una partida sin
        vigilar y la fila de la otra cuenta con el nombre cambiado. Preferimos
        que ESTA cuenta falle y lo diga.
        """
        info = dict(info)
        info["pid"] = pid
        info.setdefault("started_at", time.time())
        info.setdefault("relogs", 0)
        with self._launch_lock:
            other = self._launches.get(pid)
            if other and other["email"].lower() != info["email"].lower():
                raise RuntimeError(
                    f"Could not tell which game process belongs to this account: "
                    f"pid {pid} is already {prefs.display_name(other['email'])}. "
                    f"Launch it again in a moment.")
            self._launches[pid] = info
        self._emit_running()
        threading.Thread(target=self._monitor_launch, args=(pid,), daemon=True,
                         name=f"trove-mon-{pid}").start()
        return pid

    def _spawn_game(self, exe: Path, ticket: str, auth_server: str, info: dict,
                    *, log=print) -> int:
        """Arranca el juego y apunta la partida. DE UNA EN UNA.

        Con el loader del anti-cheat por medio no lanzamos el juego, lanzamos al
        loader; el proceso del juego hay que salir a buscarlo después, y lo único
        que lo distingue es que antes no estaba. Dos cuentas arrancando a la vez
        miran la misma lista y pueden quedarse con el mismo Trove: una partida se
        queda sin vigilar y la otra aparece con el nombre de la vecina —que es
        justo lo que pasaba al pulsar «Launch all».

        Por eso el arranque es exclusivo y con un respiro entre cuentas. Lo caro
        (actualizar, autenticar, esperar el 2FA) queda fuera y sigue en paralelo.
        """
        with self._spawn_gate:
            pause = self._last_spawn_at + _LAUNCH_GAP - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            pid = self._host().spawn(
                exe, ticket, auth_server,
                parent_process_name=self._parent_process() or "",
                exclude=self._tracked_pids(), log=log)
            self._last_spawn_at = time.monotonic()
            # Apuntarla DENTRO del cerrojo: el siguiente lanzamiento tiene que
            # ver este pid en su lista de exclusión.
            return self._track_launch(pid, info)

    def _sync_for_play(self, game_dir: Path, branch: str, email: str) -> None:
        """La fase de actualización de un lanzamiento, una por carpeta.

        Lanzar un grupo entero son N lanzamientos sobre la MISMA instalación: sin
        esto, N updaters bajando y escribiendo los mismos archivos a la vez. El
        primero actualiza y los demás esperan; si acaba de hacerse, ni eso.
        """
        key = str(game_dir)
        with self._dir_guard:
            lock = self._dir_locks.setdefault(key, threading.Lock())
        with lock:
            since = time.monotonic() - self._dir_synced.get(key, float("-inf"))
            if since < _UPDATE_FRESH_FOR:
                self.emit("play", "updating", email=email,
                          message="Files are already up to date.")
                return
            self.emit("play", "updating", email=email,
                      message=f"Updating {'PTS' if branch == 'pts' else 'Live'}...")
            self._run_sync("play", game_dir, branch, adopt=True, reset=False,
                           emit_done=False, email=email)
            self._dir_synced[key] = time.monotonic()

    def stop(self, pid: int) -> dict:
        """Cierra el juego de una instancia lanzada por nosotros.

        El pid se apunta en ``_stopping`` ANTES de matarlo: si no, el monitor
        vería una salida con código distinto de 0 y el auto-relog lo volvería a
        levantar justo después de que el usuario pidiera cerrarlo.
        """
        pid = int(pid)
        with self._launch_lock:
            known = pid in self._launches
            if known:
                self._stopping.add(pid)
        if not known:
            return {"ok": False, "error": "That process is not tracked by the launcher."}
        if not self._host().terminate(pid):
            with self._launch_lock:
                self._stopping.discard(pid)
            return {"ok": False, "error": f"Could not stop process {pid}."}
        return {"ok": True, "pid": pid}

    def _close_crash_handler(self, game_pid: int) -> None:
        """Cierra el reportador de fallos que deja el juego al caerse.

        Cuando Trove se cae abre su ventana de «envíanos el informe» y ahí se
        queda. Con auto-relog eso significa una ventana más por cada caída, hasta
        llenar la pantalla de diálogos de partidas que ya no existen.

        No sabemos cómo se llama el ejecutable en todas las instalaciones, así
        que se busca por la pista del nombre y se exige además que sea de ESTA
        muerte: o es hijo del proceso que acaba de morir, o ha aparecido después
        de morir él. Lo que ya estuviera abierto no se toca —puede ser de otra
        cuenta, o del usuario— y por eso la foto se hace aquí y no al lanzar.
        """
        host = self._host()
        try:
            before = {pid for pid, _ppid, name in host.list_processes()
                      if _CRASH_HINT in name.lower()}
        except Exception:
            return
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                procs = host.list_processes()
            except Exception:
                return
            killed = False
            for pid, ppid, name in procs:
                if _CRASH_HINT not in name.lower():
                    continue
                if pid in before and ppid != game_pid:
                    continue
                if host.terminate(pid):
                    killed = True
                    self._log(f"[relog] closed the crash reporter ({name}, pid {pid})")
            if killed:
                return
            time.sleep(1.0)

    def _monitor_launch(self, pid: int) -> None:
        code = self._host().wait_for_exit(pid)
        with self._launch_lock:
            info = self._launches.pop(pid, None)
            stopped_by_user = pid in self._stopping
            self._stopping.discard(pid)
        if not info:
            return
        uptime = time.time() - info["started_at"]
        who = prefs.display_name(info["email"])

        # Lo primero, quitar de en medio el diálogo del crash: da igual si luego
        # volvemos a entrar o no, esa ventana no pinta nada ahí. Va en su propio
        # hilo porque espera a que aparezca y no queremos retrasar el relog.
        threading.Thread(target=self._close_crash_handler, args=(pid,),
                         daemon=True, name=f"trove-crash-{pid}").start()

        if stopped_by_user:
            self._record_result(info["email"], True, "Stopped from the launcher.")
            self.emit("running", "closed", pid=pid, email=info["email"],
                      message=f"{who} stopped.")
            self._emit_running()
            return

        # Se vuelve a entrar se haya cerrado como se haya cerrado: un crash y una
        # expulsión por inactividad son lo mismo visto desde aquí (la segunda, de
        # hecho, cierra el juego limpiamente), y quien enciende el auto-relog lo
        # que quiere es que la cuenta siga dentro. Lo único que no se relanza es
        # lo que cerró el usuario —eso ya se ha atendido arriba— y lo que se
        # muere enseguida, para no encadenar arranques fallidos.
        should_relog = bool(info.get("auto_relog")) and uptime >= _MIN_UPTIME_FOR_RELOG

        if should_relog and code is None and self._still_running(pid):
            # No sabemos el código de salida y el proceso sigue en la lista: la
            # espera falló, el juego no. Relanzar aquí sería duplicar la partida.
            self._log(f"[relog] cannot watch pid {pid} any more, but it is still "
                      f"running; not relogging")
            should_relog = False

        if not should_relog:
            if info.get("auto_relog") and uptime < _MIN_UPTIME_FOR_RELOG:
                message = f"{who} exited after {int(uptime)}s — too soon to relog."
            else:
                message = f"{who} has closed."
            self.emit("running", "closed", pid=pid, email=info["email"], message=message)
            self._emit_running()
            return

        how = "closed" if code == 0 else f"exited (code {code})"
        self.emit("running", "relog", pid=pid, email=info["email"],
                  message=f"{who} {how} — signing back in...")
        self._emit_running()
        try:
            self._relaunch(info)
        except Exception as exc:
            self.emit("running", "relog_failed", email=info["email"], error=str(exc),
                      message=f"Auto-relog for {who} failed: {exc}")
            self._emit_running()

    def _still_running(self, pid: int) -> bool:
        try:
            return any(p == pid for p, _ppid, _name in self._host().list_processes())
        except Exception:
            return False

    def _relaunch(self, info: dict) -> None:
        from . import launch as launch_mod
        time.sleep(3.0)  # deja que el anti-cheat y el servicio se asienten
        creds = prefs.load_credentials(info["email"])
        password = creds.get("password", "") if creds else ""
        auth = self._make_auth(info["email"], password)
        # Sin token_provider: un relog en segundo plano no puede pedir el 2FA.
        ticket = auth.get_ticket()
        exe = self._resolve_exe(Path(info["game_path"]))
        new_info = dict(info)
        new_info["started_at"] = time.time()
        new_info["relogs"] = info.get("relogs", 0) + 1
        pid = self._spawn_game(exe, ticket,
                               launch_mod.get_auth_server(info["region"]),
                               new_info, log=self._logger("relog", info["email"]))
        self.emit("running", "relogged", pid=pid, email=info["email"],
                  message=f"{prefs.display_name(info['email'])} signed back in (pid {pid}).")

    def set_auto_relog(self, email: str, enabled: bool = True) -> dict:
        """Activa o desactiva el auto-relog de UNA cuenta.

        Se guarda en la cuenta y, si esa cuenta tiene una partida abierta, se
        aplica también a la instancia viva: cambiar la opción con el juego ya
        lanzado debe surtir efecto en ese mismo lanzamiento, no en el siguiente.
        """
        enabled = bool(enabled)
        # Comprobar ANTES de escribir: upsert_account crea la cuenta si no la
        # encuentra, así que sin esto un email desconocido daría de alta una
        # cuenta fantasma en lugar de fallar.
        if prefs.get_account(email) is None:
            return {"ok": False, "error": "That account does not exist."}
        account = prefs.upsert_account(email, auto_relog=enabled)
        if account is None:
            return {"ok": False, "error": "That account does not exist."}
        with self._launch_lock:
            for info in self._launches.values():
                if info["email"].lower() == email.lower():
                    info["auto_relog"] = enabled
        self._emit_running()
        return {"ok": True, "auto_relog": enabled}

    # --- jugar --------------------------------------------------------------

    def play(self, email: str, password: str = "", update_first: bool | None = None,
             remember_password: bool | None = None) -> dict:
        """Lanza UNA cuenta. Varias pueden estar lanzándose a la vez."""
        email = (email or "").strip()
        account = prefs.get_account(email)
        if account is None:
            return {"started": False, "error": "That account does not exist."}

        region = account.get("region", prefs.DEFAULT_REGION)
        if region not in REGION_BRANCH:
            region = prefs.DEFAULT_REGION
        branch, _kind = REGION_BRANCH[region]
        try:
            # Se valida antes de arrancar el hilo, y el fallo se registra para que
            # la fila pase a "Failed" con el motivo en vez de morir en silencio.
            game_dir = self._resolve_game_dir(region)
        except Exception as exc:
            self._record_result(email, False, str(exc))
            return {"started": False, "error": str(exc)}

        data = prefs.load()
        do_update = data.get("update_first", True) if update_first is None else bool(update_first)
        do_remember = (data.get("remember_password", True)
                       if remember_password is None else bool(remember_password))
        # Por cuenta, no global: cada una decide si debe volver a entrar sola.
        auto_relog = bool(account.get("auto_relog", False))

        busy = self._claim_account(email, "launching")
        if busy:
            return {"started": False,
                    "error": ("That account is already launching." if busy == "launching"
                              else "That account is checking its sign-in.")}

        def _work() -> None:
            from . import launch as launch_mod

            use_password = password
            if not use_password:  # sin contraseña escrita, tiramos de la guardada
                creds = prefs.load_credentials(email)
                if creds:
                    use_password = creds.get("password", "")

            if do_update:
                self._sync_for_play(game_dir, branch, email)

            exe = self._resolve_exe(game_dir)
            if not exe.exists():
                raise FileNotFoundError(
                    f"Trove executable not found in {game_dir}. "
                    f"Try Update or Repair first.")

            who = prefs.display_name(email)
            self.emit("play", "authenticating", email=email,
                      message=f"Signing in as {who}...")
            auth = self._make_auth(email, use_password)
            ticket = auth.get_ticket(token_provider=self._token_provider(email))

            if do_remember and use_password:
                prefs.save_credentials(email, use_password)

            self.emit("play", "launching", email=email, message=f"Launching {who}...")
            pid = self._spawn_game(
                exe, ticket, launch_mod.get_auth_server(region),
                {"email": email, "game_path": str(game_dir), "region": region,
                 "branch": branch, "auto_relog": auto_relog},
                log=self._logger("play", email))

            # El launcher no toca la ventana del juego: ni la trae al frente, ni
            # la restaura, ni la enumera. Nada de lo que hacemos aquí necesita
            # hablar con las ventanas de otro proceso, y no vamos a mover el
            # ratón ni el foco de quien esté jugando para ahorrarle un alt-tab.
            self._record_result(email, True, f"Launched OK (pid {pid}).")
            self.emit("play", "launched", done=True, ok=True, pid=pid, email=email,
                      message=f"{who} launched (pid {pid}).")

        def _run() -> None:
            try:
                _work()
            except Exception as exc:
                self._record_result(email, False, str(exc))
                self.emit("play", "error", done=True, ok=False, email=email,
                          error=str(exc), message=str(exc))
            finally:
                self._release_account(email)
                self.emit("play", "settled", email=email)

        threading.Thread(target=_run, daemon=True, name=f"trove-play-{email}").start()
        return {"started": True}

    # --- comprobar el inicio de sesión --------------------------------------

    def test_login(self, email: str, password: str = "",
                   remember_password: bool | None = None) -> dict:
        """Autentica contra Trion SIN lanzar el juego.

        Sirve para saber si una cuenta entra antes de darle a jugar, y deja el
        ticket en la caché, así que un lanzamiento posterior ya no vuelve a pedir
        credenciales. No toca la instalación: se puede comprobar una cuenta
        aunque el juego no esté descargado.
        """
        email = (email or "").strip()
        if prefs.get_account(email) is None:
            return {"started": False, "error": "That account does not exist."}

        do_remember = (prefs.load().get("remember_password", True)
                       if remember_password is None else bool(remember_password))

        busy = self._claim_account(email, "checking")
        if busy:
            return {"started": False,
                    "error": ("That account is launching." if busy == "launching"
                              else "That account is already being checked.")}

        def _run() -> None:
            who = prefs.display_name(email)
            try:
                use_password = password
                if not use_password:
                    creds = prefs.load_credentials(email)
                    if creds:
                        use_password = creds.get("password", "")

                self.emit("test", "authenticating", email=email,
                          message=f"Checking sign-in for {who}...")
                auth = self._make_auth(email, use_password)
                ticket = auth.get_ticket(token_provider=self._token_provider(email, "test"))

                if do_remember and use_password:
                    prefs.save_credentials(email, use_password)

                self._record_result(email, True, "Sign-in verified.")
                self.emit("test", "done", done=True, ok=True, email=email,
                          ticket_chars=len(ticket),
                          message=f"{who}: sign-in OK.")
            except Exception as exc:
                self._record_result(email, False, str(exc))
                self.emit("test", "done", done=True, ok=False, email=email,
                          error=str(exc), message=f"{who}: {exc}")
            finally:
                self._release_account(email)
                self.emit("test", "settled", email=email)

        threading.Thread(target=_run, daemon=True, name=f"trove-test-{email}").start()
        return {"started": True}

    # --- cuentas ------------------------------------------------------------

    def add_account(self, email: str, password: str = "", name: str = "",
                    region: str = "", group: str = "",
                    remember_password: bool = True) -> dict:
        email = (email or "").strip()
        if "@" not in email:
            return {"ok": False, "error": "Enter a valid email address."}
        if prefs.get_account(email):
            return {"ok": False, "error": "That account is already in the list."}

        account = prefs.upsert_account(
            email, name=name.strip(), region=region or prefs.DEFAULT_REGION,
            group=group or None)
        if password and remember_password:
            prefs.save_credentials(email, password)
        return {"ok": True, "account": account}

    def update_account(self, email: str, **fields) -> dict:
        # Igual que en set_auto_relog: upsert crea si no existe, y editar una
        # cuenta que no está no debe darla de alta.
        if prefs.get_account(email) is None:
            return {"ok": False, "error": "That account does not exist."}
        account = prefs.upsert_account(email, **fields)
        if account is None:
            return {"ok": False, "error": "That account does not exist."}
        return {"ok": True, "account": account}

    def remove_account(self, email: str) -> dict:
        prefs.remove_account(email)
        return {"ok": True}

    def logout(self, email: str) -> dict:
        """Olvida el ticket cacheado y la contraseña, pero mantiene la cuenta."""
        try:
            self._make_auth(email, "").logout()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        prefs.clear_credentials(email)
        return {"ok": True}

    def set_password(self, email: str, password: str) -> dict:
        if not password:
            prefs.clear_credentials(email)
            return {"ok": True, "has_saved_password": False}
        stored = prefs.save_credentials(email, password)
        if not stored:
            return {"ok": False,
                    "error": "DPAPI is unavailable: the password was not saved."}
        return {"ok": True, "has_saved_password": True}

    # --- grupos -------------------------------------------------------------

    def create_group(self, name: str = "") -> dict:
        return {"ok": True, "group": prefs.create_group(name)}

    def update_group(self, group_id: str, **fields) -> dict:
        group = prefs.update_group(group_id, **fields)
        if group is None:
            return {"ok": False, "error": "That group does not exist."}
        return {"ok": True, "group": group}

    def delete_group(self, group_id: str) -> dict:
        prefs.delete_group(group_id)
        return {"ok": True}

    def reorder(self, groups: list | None = None, accounts: list | None = None) -> dict:
        if groups is not None:
            prefs.reorder_groups(groups)
        if accounts is not None:
            prefs.reorder_accounts(accounts)
        return {"ok": True}

    # --- instalaciones y carpetas -------------------------------------------

    def set_install(self, path: str, kind: str = "live") -> dict:
        path = (path or "").strip()
        if path and not installs.is_valid_install(Path(path)):
            return {"ok": False, "error": f"No valid Trove found in \"{path}\"."}
        if kind == "pts":
            prefs.save(pts_game_path=path)
        else:
            prefs.save(game_path=path)
        return {"ok": True, "path": path}

    def add_custom_dir(self, path: str, name: str = "") -> dict:
        path = (path or "").strip()
        if not path:
            return {"ok": False, "error": "Empty path."}
        folder = Path(path)
        if not installs.is_valid_install(folder):
            return {"ok": False,
                    "error": f"No valid Trove executable found in \"{folder}\"."}
        data = prefs.load()
        dirs = [d for d in data.get("custom_dirs", [])
                if str(d.get("path", "")).lower() != str(folder).lower()]
        dirs.append({"path": str(folder), "name": name or folder.name})
        prefs.save(custom_dirs=dirs)
        installs.invalidate()
        return {"ok": True, "installs": installs.detect(dirs), "path": str(folder)}

    def remove_custom_dir(self, path: str) -> dict:
        data = prefs.load()
        dirs = [d for d in data.get("custom_dirs", [])
                if str(d.get("path", "")).lower() != str(path or "").lower()]
        prefs.save(custom_dirs=dirs)
        installs.invalidate()
        return {"ok": True, "installs": installs.detect(dirs)}

    def rescan_installs(self) -> dict:
        installs.invalidate()
        return {"ok": True,
                "installs": installs.detect(prefs.load().get("custom_dirs", []))}

    def open_folder(self, kind: str = "game") -> dict:
        if kind == "modcfg":
            target = trove_appdata_dir() / "ModCfgs"
            target.mkdir(parents=True, exist_ok=True)
        elif kind == "data":
            target = app_data_dir()
        elif kind == "pts":
            target = self._resolve_game_dir("PTS")
        else:
            target = self._resolve_game_dir("EU")
        _open_in_file_manager(target)
        return {"ok": True, "path": str(target)}

    def save_prefs(self, **changes) -> dict:
        prefs.save(**changes)
        return {"ok": True}


# --- utilidades de proceso -------------------------------------------------

# Lanzar, esperar y cerrar viven en ``gamehost``: en Windows son llamadas Win32
# directas y en Linux van al ayudante que corre dentro del prefijo de Wine.


def _open_in_file_manager(target: Path) -> None:
    import subprocess
    import sys

    target = Path(target)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
