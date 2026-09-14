"""The launcher's orchestrator: what the interface actually calls.

Launch model
------------

Each **account** carries its own region (NA/EU/PTS) and launches from its own
card; there is no global "active account". The region decides two things:

  * which authentication server the game points at, and
  * which installation is used: NA and EU share the Live files, while PTS needs
    the PTS folder. That is why ``_resolve_game_dir`` picks the folder from the
    region rather than from a global selector.

Concurrency
-----------

The heavy operations (check, update, repair) run on a daemon thread and only one
at a time, guarded by ``_busy``. **Launches** are the exception: several are
prepared at once, because launching a whole group is precisely the use case.
Each launch gets its own thread and its own 2FA queue, keyed by email.

Two things do NOT run in parallel, both for the same reason - they would tread
on each other over a shared resource:

  * the update phase, one per game folder (``_sync_for_play``), and
  * the start itself (``_spawn_game``), one at a time: the next account is not
    launched until the previous one is up. The game process is identified as
    "the one that was not there before", and two simultaneous starts claim the
    same one - when the anti-cheat loader does not simply fall over without
    launching anything at all.

Watching a session
------------------

After launching, a thread waits for the game to end, only to take it off the
board and say so. Nothing is ever started again on its own, and whatever the
game leaves behind when it closes - its crash reporter included - is left
exactly as the game left it.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from . import __version__
from . import gamehost, installs, prefs, trionauth
from . import vault as vault_mod
from . import updater as _updater
from . import paths as paths_mod
from . import paths as paths_mod
from .paths import app_data_dir, macaddr_path, trove_appdata_dir

# --- constants -------------------------------------------------------------

# Trion's update CDN (plain HTTP, no authentication). The double slash after
# the prefix is deliberate: it copies literally what Glyph does.
UPDATE_BASE = "http://trove-update.dyn.triongames.com"
UPDATE_PREFIX = "/kiwi-live-client-patch/"

GLYPH_USER_AGENT = "Glyph (stable-248-1-a-336302)"
GLYPH_CHANNEL = "131"
KEY_FILE = "Trove_x64.exe"

# region -> (CDN branch, installation kind it needs)
REGION_BRANCH = {"NA": ("live-us", "live"), "EU": ("live-us", "live"),
                 "PTS": ("pts", "pts")}

_CANCEL_2FA = object()          # sentinel that aborts a launch waiting for a code


class Cancelled(Exception):
    """The user asked for this launch or check to stop before it finished."""

# Breathing room between a session that is up and the next start. Not cosmetic:
# the game process is identified as "the one that was not there before", so the
# previous one must already appear in the list by the time the next looks, and
# the anti-cheat loader does not take kindly to overlapping starts.
_LAUNCH_GAP = 3.0

# Cap on waiting for a session to finish starting. Not how long we wait - we
# carry on as soon as the game is up - but how long we put up with it before
# assuming it is taking too long and moving on to the next.
_LAUNCH_READY_TIMEOUT = 120.0

# How long a just-completed sync over the same folder is taken as good. Ten
# accounts launching is not ten update checks in a row on one installation.
_UPDATE_FRESH_FOR = 120.0


class LauncherService:
    """All the launcher logic. The UI only calls methods on this class."""

    def __init__(self, emit=None, log=print):
        # ``emit(payload: dict)`` pushes an event to the interface. With no UI
        # attached yet, events are dropped quietly.
        self._emit_cb = emit
        self._log = log

        self._state_lock = threading.Lock()
        self._busy = False
        self._busy_op: str | None = None

        # One 2FA queue per email: several launches can be waiting for a code
        # at once, and each dialog must unblock its own.
        self._2fa: dict[str, queue.Queue] = {}
        self._2fa_lock = threading.Lock()

        self._launch_lock = threading.Lock()
        self._launches: dict[int, dict] = {}
        # email -> 'launching' | 'checking': what is keeping each account busy,
        # so its card reflects it and it cannot launch and check at once.
        self._account_busy: dict[str, str] = {}
        # Each launch or check gets a number, and what is cancelled is that
        # number rather than the account. The two come apart the moment a stop
        # frees the account at once: press Stop and then Launch and there are
        # two runs for one account, one of them still winding down, and a flag
        # on the email would take the new one down with the old.
        self._next_run = 0
        self._runs: dict[str, int] = {}          # email -> the run that owns it
        self._dropped: set[int] = set()          # runs told to stop
        self._nudges: dict[int, threading.Event] = {}   # runs that can be woken
        # pids closed from the launcher, so their end is reported as a stop
        # rather than as the game closing on its own.
        self._stopping: set[int] = set()

        # Launches are prepared in parallel but START one at a time: see
        # _spawn_game. The stamp is of the last start, to leave the gap.
        self._spawn_gate = threading.Lock()
        self._last_spawn_at = 0.0

        # One game folder, one update at a time: launching ten accounts cannot
        # put ten updaters writing over the same files.
        self._dir_locks: dict[str, threading.Lock] = {}
        self._dir_synced: dict[str, float] = {}
        self._dir_guard = threading.Lock()

        # email -> {"ok": bool, "detail": str}: the result of the last real
        # attempt (check or launch) IN THIS SESSION. Deliberately in memory and
        # not on disk: on opening the launcher we do not know whether an account
        # signs in until it is tried, so it starts grey instead of lying with a
        # "Ready".
        self._last_result: dict[str, dict] = {}

    # --- events towards the UI ------------------------------------------------

    def emit(self, op: str, stage: str, **fields) -> None:
        payload = {"op": op, "stage": stage}
        payload.update(fields)
        if self._emit_cb is None:
            return
        try:
            self._emit_cb(payload)
        except Exception:
            pass  # the UI is not listening (window closing): nothing to do

    def _logger(self, op: str, email: str = ""):
        """A logger tagged with the operation it belongs to.

        It only calls ``self._log``: that already reaches the log panel (see
        ``_make_logger`` in main.py). Emitting here as well put every line in
        the panel twice.
        """
        def _log(message) -> None:
            self._log(f"[{op}] {message}")
        return _log

    def _progress(self, op: str, stage: str = "downloading", email: str = ""):
        """Progress callback throttled to one event every 150 ms, but which
        always lets the last one through (else the bar stops half-way)."""
        last = [0.0]

        def _cb(seen: int, total: int, downloaded: int) -> None:
            now = time.monotonic()
            if total and now - last[0] < 0.15 and seen < total:
                return
            last[0] = now
            self.emit(op, stage, current=seen, total=total, downloaded=downloaded,
                      email=email)
        return _cb

    # --- worker-thread scheduling ----------------------------------

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
        """Runs ``target()`` on a daemon thread if nothing else is in progress.

        Maintenance only: launches go their own way and do not take this lock,
        because several accounts may start at once.
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

    # --- installations ------------------------------------------------------

    def install_list(self) -> list[dict]:
        """Installations already known. If nothing has been scanned yet, returns
        what there is and kicks the scan off in the background: the window must
        not wait for a sleeping disk to wake up."""
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
        """The game folder that goes with a region.

        NA and EU use the Live files; PTS needs its own folder. If the user has
        not set a PTS folder, we take the first detected installation of that
        kind before giving up.
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

    # --- state for the interface --------------------------------------------

    def _account_state(self, email: str, instance: dict | None) -> dict:
        """An account's state: ``{"status", "detail"}``.

        The possible states, most to least specific:

        ``running``   the game is open and we know its pid.
        ``launching`` / ``checking``  an operation is in flight.
        ``failed``    this session's last attempt failed; ``detail`` says why.
        ``ready``     verified this session: the account signs in.
        ``pending``   data missing (no saved password and no cached ticket).
        ``unknown``   it has credentials, but we have not checked them yet.

        The difference between ``unknown`` and ``ready`` is deliberate: having a
        saved password does not prove Trion accepts it, so nothing goes green
        until it has actually been verified.
        """
        key = email.lower()
        with self._launch_lock:
            busy = self._account_busy.get(key)
        if busy:
            return {"status": busy, "detail": ""}
        if instance:
            return {"status": "running",
                    "detail": f"pid {instance['pid']} · up {instance['uptime']}s"}

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
                "detail": "No saved password and no cached session - it will ask when you launch."}

    def _record_result(self, email: str, ok: bool, detail: str = "") -> None:
        self._last_result[email.lower()] = {"ok": ok, "detail": detail}

    def state(self) -> dict:
        # Anything that happened before there was a window to say it in. The
        # buffer empties itself, so this reports only once.
        for message in paths_mod.drain_notes():
            self.emit("app", "log", message=message)

        data = prefs.load()
        running = self.running_list()
        by_email = {i["email"].lower(): i for i in running}

        accounts = []
        for account in prefs.accounts():
            email = account.get("email", "")
            instance = by_email.get(email.lower())
            accounts.append({
                **account,
                "flagged": bool(account.get("flagged", False)),
                "label": account.get("name") or prefs.mask_email(email),
                "masked": prefs.mask_email(email),
                **self._account_state(email, instance),
                "pid": instance["pid"] if instance else None,
                "uptime": instance["uptime"] if instance else 0,
                "has_saved_password": prefs.has_saved_password(email),
            })

        return {
            "version": __version__,
            "groups": prefs.groups(),
            "accounts": accounts,
            "installs": self.install_list(),
            "game_path": data.get("game_path", ""),
            "pts_game_path": data.get("pts_game_path", ""),
            "hide_emails": bool(data.get("hide_emails", True)),
            "remember_password": bool(data.get("remember_password", True)),
            "wine_binary": data.get("wine_binary", ""),
            "wine_prefix": data.get("wine_prefix", ""),
            "theme": data.get("theme") or dict(prefs.DEFAULTS["theme"]),
            # The background image's settings, not the image: it is fetched
            # once with get_wallpaper, and megabytes of base64 have no business
            # travelling with every refresh of the board.
            "wallpaper": {**prefs.DEFAULTS["wallpaper"],
                          **(data.get("wallpaper") or {})},
            "logo": {**prefs.DEFAULTS["logo"], **(data.get("logo") or {})},
            "mods": {**prefs.DEFAULTS["mods"], **(data.get("mods") or {})},
            "sidebar": {**prefs.DEFAULTS["sidebar"], **(data.get("sidebar") or {})},
            # Whatever a local addition asked us to keep for it. Opaque
            # here: it is saved and given back, and nothing reads it.
            "local": data.get("local") or {},
            "update_first": bool(data.get("update_first", True)),
            "folders": self._folders(data),
            "versions": self._versions(data),
            # What launches here and where secrets are kept: the interface
            # needs it to explain why something is not possible.
            "host": self._host().status(),
            "vault": vault_mod.status(),
        }

    def _folders(self, data: dict) -> list[dict]:
        """The paths the interface shows under Settings -> Folders.

        Read-only: opening them is still ``open_folder``'s job. They are listed
        even when they do not exist yet so the user can see where they will be.
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
        """The version applied locally per branch, for the maintenance view."""
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

    # --- check / update / repair -----------------------------------

    def _run_sync(self, op: str, game_dir: Path, branch: str, *, adopt: bool,
                  reset: bool, emit_done: bool = True, email: str = "",
                  watch: int = 0) -> dict:
        """The shared body of update (adopt) and repair (wipe state and
        re-download). With ``emit_done=False`` the final event is not emitted:
        that is used when this sync is merely the update *phase* of a launch, so
        the UI does not think the whole Play has finished.

        ``watch`` is a run whose stop request should interrupt this. The
        check rides on the progress callback, which the updater calls once per
        file, so an update is given up between files and never mid-write. Only
        launches pass it: an update the user started themselves is not stopped
        by a card's stop button.
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
            forward = self._progress(op, email=email)

            def progress(*args):
                if watch:
                    self._still_wanted(watch)
                forward(*args)

            result = up.update(key_file=KEY_FILE, adopt=adopt, progress=progress)
        finally:
            up.close()

        if emit_done:
            ok = result["failed"] == 0
            if result.get("skipped"):
                message = f"Already on the latest version ({result['version']})."
            elif ok:
                message = (f"Done: {result['version']} - {result['downloaded']} downloaded, "
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
        """What TrionAuth calls when the server demands a two-step code: it
        tells the interface and blocks THAT operation until the code arrives."""
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

    # --- per-account busy flag ----------------------------------------------

    def _claim_account(self, email: str, what: str) -> tuple[str | None, int]:
        """Marks the account busy. Returns `(the reason it already was, run)`."""
        with self._launch_lock:
            current = self._account_busy.get(email.lower())
            if current:
                return current, 0
            self._next_run += 1
            run = self._next_run
            self._account_busy[email.lower()] = what
            self._runs[email.lower()] = run
            self._nudges[run] = threading.Event()
        return None, run

    def _release_account(self, email: str, run: int) -> None:
        """Give the account back, if this run still holds it.

        The `if` is the whole point: a stop hands the account back straight
        away, and by the time this run notices, the account may already belong
        to the launch the user started next.
        """
        with self._launch_lock:
            if self._runs.get(email.lower()) == run:
                self._account_busy.pop(email.lower(), None)
                self._runs.pop(email.lower(), None)
            self._dropped.discard(run)
            self._nudges.pop(run, None)

    def _asked_to_stop(self, run: int) -> bool:
        with self._launch_lock:
            return run in self._dropped

    def _still_wanted(self, run: int) -> None:
        """Raise if this run has been told to stop.

        Asked between the steps of a launch. A thread already inside an HTTPS
        request to Trion cannot be interrupted and a file cannot be abandoned
        mid-write, so those two finish - but the account is free from the
        moment Stop is pressed, and nothing this run does afterwards reaches
        the user: it cannot leave a game running and it writes no result.
        """
        if self._asked_to_stop(run):
            raise Cancelled()

    def _pause(self, run: int, seconds: float) -> None:
        """Wait, unless this run is stopped first."""
        nudge = self._nudges.get(run)
        if nudge is None:
            time.sleep(seconds)
        elif nudge.wait(seconds):
            raise Cancelled()

    def cancel_account(self, email: str) -> dict:
        """Stop a launch or a check that is under way. Takes effect at once.

        "At once" is the point, and it is three separate things: the account is
        handed back here and not when the thread gets round to noticing, so the
        card is free immediately; any game that has already come up is closed;
        and anything the run is sleeping on - its turn to start, a code dialog -
        is woken so it does not sit there for its full time.

        What cannot be cut short is a request already in flight and a file
        already being written. Both finish, and neither reaches anybody: the run
        is marked dropped, so it records no result and closes any game it still
        manages to start.
        """
        key = (email or "").strip().lower()
        with self._launch_lock:
            busy = self._account_busy.get(key)
            run = self._runs.get(key, 0)
            if busy:
                self._dropped.add(run)
                # Handed back HERE. Everything else about stopping is the
                # thread unwinding on its own time; this is the part the user
                # is looking at.
                self._account_busy.pop(key, None)
                self._runs.pop(key, None)
            nudge = self._nudges.get(run)
            started = [i["pid"] for i in self._launches.values()
                       if i["email"].lower() == key]
        if not busy:
            # Not an error: what was asked for is that it not be launching, and
            # it is not. Says so quietly rather than in red, because the button
            # can be pressed in the instant between the last step and the end.
            return {"ok": True, "was": None}

        if nudge is not None:
            nudge.set()
        # A code dialog waiting for an answer would otherwise hold the thread
        # at a step that never comes back on its own.
        self.cancel_2fa(email)
        # And a session already up is closed: from the moment the game has a
        # pid the launch thread is inside ``wait_until_ready``, which sits
        # there for up to two minutes and asks nothing while it does.
        for pid in started:
            self.stop(pid)
        self.emit("play", "log",
                  message=f"Stopped {prefs.display_name(email)}.")
        self.emit("play", "settled", email=email)
        return {"ok": True, "was": busy}

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

    # --- tracking launched processes ----------------------------------

    def _tracked_pids(self) -> set[int]:
        """Pids of sessions we already track. They are excluded when resolving a
        new launch's process: if two accounts start at once and one has to fall
        back to the last resort ("any new Trove"), without this it could claim
        the other's session."""
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
            "uptime": int(now - i["started_at"]),
        } for i in items]

    def _emit_running(self) -> None:
        # No body: the interface answers by asking for the whole state, which
        # is where what it paints comes from. Sending the list here meant
        # sending it twice.
        self.emit("running", "update")

    def _host(self) -> "gamehost.GameHost":
        """Whoever knows how to launch here: Windows directly, or Wine."""
        return gamehost.host(log=self._log)

    def _track_launch(self, pid: int, info: dict) -> int:
        """Records a session and puts a watcher on it. Returns its pid.

        If that pid already belongs to another account we do not overwrite it:
        that would mean the resolution got it wrong, and keeping the latest would
        leave one session unwatched and the other account's card under the wrong
        name. We would rather THIS account failed and said so.
        """
        info = dict(info)
        info["pid"] = pid
        info.setdefault("started_at", time.time())
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
                    *, log=print, watch: int = 0) -> int:
        """Starts the game and records the session. ONE AT A TIME.

        With the anti-cheat loader in the way we do not launch the game, we
        launch the loader; the game's process has to be hunted down afterwards,
        and the only thing that distinguishes it is that it was not there before.
        Two accounts starting at once look at the same list and can settle on the
        same Trove: one session goes unwatched and the other shows up under its
        neighbour's name - which is exactly what pressing "Launch all" did.

        And the turn is not released on start, but when that session is UP.
        Launching the next while the previous one is still coming up is what
        makes the loader fall over without launching anything (exit code 1021,
        for one) and its account end up adopting the neighbour's session.

        The expensive part - updating, authenticating, waiting for 2FA - stays
        outside and still happens in parallel: what queues up is the start.

        ``watch`` is the run whose stop button reaches in here. This is the
        longest stretch of a launch that has nothing to check: up to three
        seconds waiting for its turn and then up to two minutes waiting for the
        session to come up, all of it with the card still reading "Logging in".
        A stop pressed in that window used to do nothing at all and the game
        arrived anyway - which is exactly what "it does not work" looked like.
        """
        host = self._host()
        with self._spawn_gate:
            pause = self._last_spawn_at + _LAUNCH_GAP - time.monotonic()
            if pause > 0:
                # Woken by a stop rather than slept through: this is up to three
                # seconds of doing nothing, and it is where a press often lands.
                self._pause(watch, pause)
            try:
                pid = host.spawn(
                    exe, ticket, auth_server,
                    exclude=self._tracked_pids(), log=log)
            except Exception:
                # A failed start counts towards the gap too: if the loader has
                # just fallen over, chaining the next one instantly is the best
                # way to make it fall over again.
                self._last_spawn_at = time.monotonic()
                raise
            # Record it INSIDE the lock: the next launch has to see this pid in
            # its exclusion list.
            self._track_launch(pid, info)
            if watch and self._asked_to_stop(watch):
                # Stop was pressed while this was on its way up. The game is
                # here now, so closing it IS what the press meant: leaving it
                # open hands over the session the user just said they did not
                # want. Checked here, the earliest point at which there is a
                # pid to close, rather than after the wait below - which is two
                # minutes of nothing happening.
                log(f"[play] stopped while starting: closing pid {pid}")
                self._last_spawn_at = time.monotonic()
                self.stop(pid)
                raise Cancelled()
            if not host.wait_until_ready(pid, _LAUNCH_READY_TIMEOUT, log=log):
                log(f"[play] the game (pid {pid}) closed while it was starting")
            # The gap counts from when it is up, not from when it was executed:
            # that is what the anti-cheat is given to settle.
            self._last_spawn_at = time.monotonic()
            return pid

    def _sync_for_play(self, game_dir: Path, branch: str, email: str,
                       run: int = 0) -> None:
        """The update phase of a launch, one per folder.

        Launching a whole group is N launches over the SAME installation:
        without this, N updaters downloading and writing the same files at once.
        The first updates and the rest wait; if it has just been done, not even
        that.
        """
        key = str(game_dir)
        with self._dir_guard:
            lock = self._dir_locks.setdefault(key, threading.Lock())
        # Not a plain `with lock:`. Another account can be updating this same
        # folder, and waiting for it is otherwise a wait nothing can interrupt -
        # taken in slices, a stop pressed during it is answered in a quarter of
        # a second instead of whenever the other launch finishes.
        while not lock.acquire(timeout=0.25):
            self._still_wanted(run)
        try:
            self._still_wanted(run)
            since = time.monotonic() - self._dir_synced.get(key, float("-inf"))
            if since < _UPDATE_FRESH_FOR:
                self.emit("play", "updating", email=email,
                          message="Files are already up to date.")
                return
            self.emit("play", "updating", email=email,
                      message=f"Updating {'PTS' if branch == 'pts' else 'Live'}...")
            self._run_sync("play", game_dir, branch, adopt=True, reset=False,
                           emit_done=False, email=email, watch=run)
            self._dir_synced[key] = time.monotonic()
        finally:
            lock.release()

    def stop(self, pid: int) -> dict:
        """Closes the game for an instance we launched.

        The pid is recorded in ``_stopping`` before it is closed, so the watcher
        reports the end as a stop from here and not as the game closing itself.
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

    def _monitor_launch(self, pid: int) -> None:
        """Waits for a session to end, then takes it off the board.

        That is all it does. The game is never started again from here, and
        whatever it opens on its way out - its crash reporter, most of all - is
        left exactly as the game left it.
        """
        self._host().wait_for_exit(pid)
        with self._launch_lock:
            info = self._launches.pop(pid, None)
            stopped_by_user = pid in self._stopping
            self._stopping.discard(pid)
        if not info:
            return
        who = prefs.display_name(info["email"])
        if stopped_by_user:
            self._record_result(info["email"], True, "Stopped from the launcher.")
            message = f"{who} stopped."
        else:
            message = f"{who} has closed."
        self.emit("running", "closed", pid=pid, email=info["email"], message=message)
        self._emit_running()

    # --- playing --------------------------------------------------------------

    def play(self, email: str, password: str = "", update_first: bool | None = None,
             remember_password: bool | None = None) -> dict:
        """Launches ONE account. Several can be launching at once."""
        email = (email or "").strip()
        account = prefs.get_account(email)
        if account is None:
            return {"started": False, "error": "That account does not exist."}

        region = account.get("region", prefs.DEFAULT_REGION)
        if region not in REGION_BRANCH:
            region = prefs.DEFAULT_REGION
        branch, _kind = REGION_BRANCH[region]
        try:
            # Validated before starting the thread, and the failure recorded so
            # the card turns "Failed" with the reason instead of dying quietly.
            game_dir = self._resolve_game_dir(region)
        except Exception as exc:
            self._record_result(email, False, str(exc))
            return {"started": False, "error": str(exc)}

        data = prefs.load()
        do_update = data.get("update_first", True) if update_first is None else bool(update_first)
        do_remember = (data.get("remember_password", True)
                       if remember_password is None else bool(remember_password))

        busy, run = self._claim_account(email, "launching")
        if busy:
            return {"started": False,
                    "error": ("That account is already launching." if busy == "launching"
                              else "That account is checking its sign-in.")}

        def _work() -> None:
            from . import launch as launch_mod

            use_password = password
            if not use_password:  # nothing typed, fall back to the saved one
                creds = prefs.load_credentials(email)
                if creds:
                    use_password = creds.get("password", "")

            self._still_wanted(run)
            if do_update:
                self._sync_for_play(game_dir, branch, email, run)

            self._still_wanted(run)
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

            # Saved only when Trion has just accepted it. With a cached ticket
            # the password is never sent, so a mistyped one would otherwise be
            # stored over the good one and not fail until the ticket ran out.
            if do_remember and use_password and auth.used_password:
                prefs.save_credentials(email, use_password)

            # The last chance to stop: after this the game exists, and what
            # closes it is Stop, not this.
            self._still_wanted(run)
            self.emit("play", "launching", email=email, message=f"Launching {who}...")
            pid = self._spawn_game(
                exe, ticket, launch_mod.get_auth_server(region),
                {"email": email, "game_path": str(game_dir), "region": region,
                 "branch": branch},
                log=self._logger("play", email), watch=run)

            # And once more on the way out, for a press that landed during the
            # wait for the session to be ready.
            if self._asked_to_stop(run):
                self.stop(pid)
                raise Cancelled()

            # The launcher never touches the game's window: it does not bring
            # it forward, restore it or enumerate it. Nothing we do here needs to
            # talk to another process's windows, and we are not moving the mouse
            # or the focus of whoever is playing to save them an alt-tab.
            self._still_wanted(run)          # nothing is recorded for a stopped run
            self._record_result(email, True, f"Launched OK (pid {pid}).")
            self.emit("play", "launched", done=True, ok=True, pid=pid, email=email,
                      message=f"{who} launched (pid {pid}).")

        def _run() -> None:
            try:
                _work()
            except Cancelled:
                # No result recorded: the account is left exactly as it was
                # before the button was pressed, not marked as having failed.
                self.emit("play", "cancelled", done=True, ok=False, email=email,
                          message=f"{prefs.display_name(email)}: launch stopped.")
            except Exception as exc:
                # A run the user stopped is allowed to fail in peace. Its
                # request was already in flight when Stop was pressed, and
                # reporting that it failed would put "Failed" on a card the
                # user has already put back to idle themselves.
                if self._asked_to_stop(run):
                    self.emit("play", "cancelled", done=True, ok=False, email=email,
                              message=f"{prefs.display_name(email)}: launch stopped.")
                else:
                    self._record_result(email, False, str(exc))
                    self.emit("play", "error", done=True, ok=False, email=email,
                              error=str(exc), message=str(exc))
            finally:
                self._release_account(email, run)
                self.emit("play", "settled", email=email)

        threading.Thread(target=_run, daemon=True, name=f"trove-play-{email}").start()
        return {"started": True}

    # --- checking the sign-in --------------------------------------

    def test_login(self, email: str, password: str = "",
                   remember_password: bool | None = None) -> dict:
        """Authenticates against Trion WITHOUT launching the game.

        It answers whether an account signs in before pressing play, and leaves
        the ticket in the cache, so a later launch does not ask for credentials
        again. It does not touch the installation: an account can be checked
        even when the game is not downloaded.
        """
        email = (email or "").strip()
        if prefs.get_account(email) is None:
            return {"started": False, "error": "That account does not exist."}

        do_remember = (prefs.load().get("remember_password", True)
                       if remember_password is None else bool(remember_password))

        busy, run = self._claim_account(email, "checking")
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

                self._still_wanted(run)
                self.emit("test", "authenticating", email=email,
                          message=f"Checking sign-in for {who}...")
                auth = self._make_auth(email, use_password)
                ticket = auth.get_ticket(token_provider=self._token_provider(email, "test"))

                self._still_wanted(run)
                if do_remember and use_password and auth.used_password:
                    prefs.save_credentials(email, use_password)

                self._still_wanted(run)
                self._record_result(email, True, "Sign-in verified.")
                self.emit("test", "done", done=True, ok=True, email=email,
                          ticket_chars=len(ticket),
                          message=f"{who}: sign-in OK.")
            except Cancelled:
                self.emit("test", "cancelled", done=True, ok=False, email=email,
                          message=f"{who}: check stopped.")
            except Exception as exc:
                if self._asked_to_stop(run):
                    self.emit("test", "cancelled", done=True, ok=False, email=email,
                              message=f"{who}: check stopped.")
                else:
                    self._record_result(email, False, str(exc))
                    self.emit("test", "done", done=True, ok=False, email=email,
                              error=str(exc), message=f"{who}: {exc}")
            finally:
                self._release_account(email, run)
                self.emit("test", "settled", email=email)

        threading.Thread(target=_run, daemon=True, name=f"trove-test-{email}").start()
        return {"started": True}

    # --- accounts ------------------------------------------------------------

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
        # upsert creates the account when it is missing, and editing an account
        # that is not there must not register it.
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
        """Forgets the cached ticket and the password, but keeps the account."""
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

    # --- groups -------------------------------------------------------------

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

    # --- installations and folders -------------------------------------------

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


# --- process helpers -------------------------------------------------

# Launching, waiting and closing live in ``gamehost``: on Windows they are
# direct Win32 calls, on Linux they go to the helper running inside the Wine
# prefix.


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
