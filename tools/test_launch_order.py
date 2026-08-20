"""Lanzar varias cuentas: que cada una acabe con SU partida, y el auto-relog.

Esto es lo que se rompía con «Launch all»: los lanzamientos salían a la vez, y
como el proceso del juego se identifica por «el que no estaba antes», dos cuentas
se adjudicaban el mismo Trove. Resultado: una partida sin vigilar y la otra
mostrada con el nombre de la vecina.

Aquí no hace falta ni Wine ni el juego: se sustituye el anfitrión por uno de
mentira que imita lo único que importa —que el pid del juego hay que salir a
buscarlo y tarda en aparecer— y se comprueba el comportamiento del servicio.

    python tools/test_launch_order.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Antes de importar nada de core: sus datos van a una carpeta desechable.
TMP = Path(tempfile.mkdtemp(prefix="trovelaunch-"))
os.environ["XDG_DATA_HOME"] = str(TMP / "data")
os.environ["APPDATA"] = str(TMP / "data")

from core import prefs, service  # noqa: E402

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("  OK  " if cond else " FALLA") + " | " + label)


def waitfor(cond, timeout=25.0, step=0.05):
    """Espera a que se cumpla algo, y devuelve si se cumplió."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(step)
    return False


class FakeAuth:
    """Trion, sin Trion."""

    def __init__(self, email):
        self.email = email

    def get_ticket(self, token_provider=None):
        time.sleep(0.05)
        return f"TICKET({self.email})"

    def has_valid_cache(self):
        return True


class FakeHost:
    """Un Windows de mentira: procesos, arranques y muertes.

    Reproduce lo que hace el de verdad y que es la fuente del problema: al
    arrancar no se obtiene el pid del juego, se obtiene más tarde y buscándolo
    entre los procesos que hay, descartando los que ya se vigilan.
    """

    kind = "fake"

    def __init__(self):
        self.lock = threading.Lock()
        self.procs: dict[int, tuple[int, str]] = {}
        self.exits: dict[int, int] = {}
        self.events: dict[int, threading.Event] = {}
        self.spawns: list[tuple[float, int]] = []
        self.next_pid = 4000
        self.delay = 0.2          # lo que tarda el loader en levantar el juego
        self.crash_on_death = False

    # -- lo que usa el servicio --
    def check(self):
        return

    def status(self):
        return {"kind": self.kind, "ready": True, "detail": ""}

    def spawn(self, exe, ticket, auth_server, *, parent_process_name="",
              exclude=None, log=print):
        exclude = set(exclude or ())
        with self.lock:
            self.next_pid += 1
            pid = self.next_pid
            self.procs[pid] = (7, "Trove_x64.exe")
            self.events[pid] = threading.Event()
        time.sleep(self.delay)
        with self.lock:
            mine = [p for p, (_pp, name) in sorted(self.procs.items())
                    if name.lower() == "trove_x64.exe" and p not in exclude]
        got = mine[0] if mine else pid
        self.spawns.append((time.monotonic(), got))
        return got

    def wait_for_exit(self, pid):
        event = self.events.get(pid)
        if event is None:
            return None
        event.wait()
        return self.exits.get(pid)

    def terminate(self, pid):
        with self.lock:
            if pid not in self.procs:
                return False
            self.procs.pop(pid)
            self.exits.setdefault(pid, -1)
            event = self.events.get(pid)
        if event:
            event.set()
        return True

    def list_processes(self):
        with self.lock:
            return [(pid, ppid, name) for pid, (ppid, name) in self.procs.items()]

    def pids_by_name(self, name):
        return {p for p, _pp, n in self.list_processes() if n.lower() == name.lower()}

    def close(self):
        return

    # -- para el test --
    def die(self, pid, code):
        """Mata una partida como la mataría el juego: con su código de salida."""
        with self.lock:
            self.procs.pop(pid, None)
            self.exits[pid] = code
            event = self.events.get(pid)
            if self.crash_on_death and code != 0:
                self.next_pid += 1
                self.procs[self.next_pid] = (pid, "Trove.CrashHandler.exe")
        if event:
            event.set()

    def add(self, name, ppid=7):
        with self.lock:
            self.next_pid += 1
            self.procs[self.next_pid] = (ppid, name)
            return self.next_pid


class TestService(service.LauncherService):
    def _make_auth(self, email, password=""):
        return FakeAuth(email)


# --- montaje ----------------------------------------------------------------

game_dir = TMP / "game"
game_dir.mkdir(parents=True)
(game_dir / "Trove_x64.exe").write_bytes(b"MZ")   # sólo tiene que existir

prefs.save(game_path=str(game_dir), update_first=False, remember_password=False)
EMAILS = ["wolf@example.com", "ceruledge@example.com",
          "mystic@example.com", "sayro@example.com"]
for i, email in enumerate(EMAILS):
    prefs.upsert_account(email, name=email.split("@")[0].title(), region="EU")

host = FakeHost()
service.gamehost.host = lambda log=print: host    # el anfitrión de este equipo
svc = TestService(log=lambda *a: None)

# En producción el respiro entre arranques es de segundos; aquí interesa el
# ORDEN, no la espera, así que se acorta. Que el de verdad exista se comprueba
# aparte, que para eso es una constante.
check("hay un respiro real entre arranques", service._LAUNCH_GAP >= 2.0)
service._LAUNCH_GAP = 0.3

# --- 1) cuatro cuentas a la vez, como «Launch all» --------------------------
for email in EMAILS:
    svc.play(email=email, password="pw")

check("las cuatro cuentas llegan a estar en marcha",
      waitfor(lambda: len(svc.running_list()) == len(EMAILS)))

running = {i["email"]: i["pid"] for i in svc.running_list()}
check("cada cuenta tiene su propio proceso",
      len(set(running.values())) == len(EMAILS))
check("y ninguna se queda por el camino", set(running) == set(EMAILS))

# Lo que de verdad se rompía: la fila decía un nombre y jugaba otro. El
# anfitrión sabe qué pid entregó a cada arranque, y el servicio tiene que
# haberlos repartido en ese mismo orden.
handed = [pid for _t, pid in host.spawns]
check("no se entregó dos veces el mismo proceso",
      len(handed) == len(set(handed)) == len(EMAILS))

gaps = [b - a for (a, _p1), (b, _p2) in zip(host.spawns, host.spawns[1:])]
check("los arranques van de uno en uno, no todos a la vez",
      all(gap >= service._LAUNCH_GAP for gap in gaps))

# --- 2) cerrar una no toca a las demás --------------------------------------
victim = running[EMAILS[0]]
svc.stop(victim)
check("la cuenta cerrada desaparece de la lista",
      waitfor(lambda: victim not in {i["pid"] for i in svc.running_list()}))
check("y las otras tres siguen jugando", len(svc.running_list()) == 3)
check("cerrar desde el launcher NO vuelve a entrar",
      not waitfor(lambda: any(i["email"] == EMAILS[0] for i in svc.running_list()),
                  timeout=3.0))

for info in list(svc.running_list()):
    svc.stop(info["pid"])
waitfor(lambda: not svc.running_list())

# --- 3) la actualización, una por carpeta -----------------------------------
syncs = []
original_sync = svc._run_sync


def counting_sync(op, folder, branch, **kw):
    syncs.append(str(folder))
    time.sleep(0.3)
    return {"failed": 0, "version": "x", "downloaded": 0, "unchanged": 0}


svc._run_sync = counting_sync
for email in EMAILS:
    svc.play(email=email, password="pw", update_first=True)
check("con varias cuentas sobre la misma carpeta se actualiza una vez",
      waitfor(lambda: len(svc.running_list()) == len(EMAILS)) and len(syncs) == 1)
svc._run_sync = original_sync
for info in list(svc.running_list()):
    svc.stop(info["pid"])
waitfor(lambda: not svc.running_list())

# --- 4) auto-relog ----------------------------------------------------------
service._MIN_UPTIME_FOR_RELOG = 0.2
prefs.upsert_account(EMAILS[0], auto_relog=True)
svc.play(email=EMAILS[0], password="pw")
check("la cuenta con auto-relog arranca",
      waitfor(lambda: len(svc.running_list()) == 1))
first = svc.running_list()[0]["pid"]
time.sleep(0.5)

# Cierre LIMPIO: es lo que pasa cuando el servidor te echa por inactividad, y
# antes no se relanzaba. Ahora sí.
host.die(first, 0)
check("un cierre normal también vuelve a entrar",
      waitfor(lambda: [i for i in svc.running_list()
                       if i["email"] == EMAILS[0] and i["pid"] != first], timeout=30))
second = svc.running_list()[0]["pid"]
check("y la partida nueva cuenta el relog",
      svc.running_list()[0]["relogs"] == 1)

# --- 5) el diálogo de crash no se queda ahí ---------------------------------
host.crash_on_death = True
mine_own = host.add("otro-crashreporter.exe")     # ajeno y ya abierto: no se toca
time.sleep(0.2)
host.die(second, 1)
check("el reportador de fallos del juego se cierra",
      waitfor(lambda: not [n for _p, _pp, n in host.list_processes()
                           if n == "Trove.CrashHandler.exe"], timeout=25))
check("y no se toca lo que ya estaba abierto",
      mine_own in {p for p, _pp, _n in host.list_processes()})

check("tras el crash, vuelve a entrar",
      waitfor(lambda: [i for i in svc.running_list()
                       if i["email"] == EMAILS[0] and i["pid"] != second], timeout=30))

# --- 6) morir enseguida no encadena arranques -------------------------------
service._MIN_UPTIME_FOR_RELOG = 3600.0
host.crash_on_death = False
third = svc.running_list()[0]["pid"]
host.die(third, 1)
# Primero se vacía la lista (el vigilante tarda un instante en enterarse) y
# luego se le da tiempo de sobra para volver a entrar, que es lo que NO debe
# pasar: el relog espera unos segundos antes de relanzar.
check("la partida caída sale de la lista", waitfor(lambda: not svc.running_list()))
time.sleep(6.0)
check("una partida que se cae al instante no se relanza", not svc.running_list())

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
