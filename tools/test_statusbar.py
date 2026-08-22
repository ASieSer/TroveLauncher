"""El indicador de abajo: la barra y el texto se encienden y se apagan JUNTOS.

Lo que se veía mal: al terminar de lanzar una cuenta desaparecía la barra
animada pero el «Signing in …» se quedaba unos segundos más, como si siguiera
pasando algo. Y al revés: una operación larga acababa con la barra girando y sin
texto, porque el mensaje se borraba solo a los 7 segundos.

Aquí se carga la interfaz de verdad en un navegador, con un backend de mentira,
y se le meten los mismos eventos que emite ``core/service.py``.

Hace falta Playwright con Chromium:

    pip install playwright && playwright install chromium
    python tools/test_statusbar.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright: pip install playwright && playwright install chromium")
    sys.exit(2)

# Un estado mínimo: lo que se mira aquí es la barra de estado, no las tarjetas.
STATE = {
    "groups": [], "accounts": [], "installs": [], "regions": ["NA", "EU", "PTS"],
    "game_path": "/games/Trove/Live", "pts_game_path": "", "hide_emails": True,
    "remember_password": True, "wine_binary": "", "wine_prefix": "",
    "theme": {"accent": "#22c55e", "customs": [], "stars": True,
              "font": "system", "club": ""},
    "update_first": True, "reparent_glyph": False, "folders": [], "versions": {},
    "host": {"kind": "windows", "ready": True, "detail": ""},
    "vault": {"backend": "DPAPI", "available": True, "detail": ""},
    "busy": False, "busy_op": None, "running": [],
}

# El puente de pywebview, de mentira: todo contesta que sí.
STUB = """
window.pywebview = { api: new Proxy({}, { get: (t, name) => (...args) => {
  if (name === 'get_state') return Promise.resolve({ ok: true, state: %s });
  return Promise.resolve({ ok: true });
}})};
""" % json.dumps(STATE)

ok = True


def check(label, cond):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " FALLA") + " | " + label)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass          # la salida es la lista de comprobaciones, no un access.log


# La interfaz se sirve por HTTP: desde file:// el navegador bloquea por CORS las
# máscaras CSS, y eso es ruido que no tiene nada que ver con lo que se prueba.
handler = partial(Quiet, directory=str(ROOT / "web"))
server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

chromium = os.environ.get("CHROMIUM", "")
with sync_playwright() as p:
    browser = p.chromium.launch(**({"executable_path": chromium} if chromium else {}))
    page = browser.new_page(viewport={"width": 1000, "height": 700})
    page.add_init_script(STUB)
    page.goto(url)
    page.wait_for_timeout(700)

    def fire(payload):
        """Un evento del backend, tal cual sale de service.emit()."""
        page.evaluate("p => window.__launcherEvent(p)", payload)
        page.wait_for_timeout(120)

    def bar():
        return not page.locator("#progress").evaluate(
            "e => e.classList.contains('hidden')")

    def msg():
        return page.locator("#status-msg").inner_text()

    def msg_class():
        return page.locator("#status-msg").get_attribute("class") or ""

    # --- una cuenta ---------------------------------------------------------
    fire({"op": "play", "stage": "authenticating", "email": "wolf@example.com",
          "message": "Signing in Wolf..."})
    check("mientras arranca hay barra y texto",
          bar() and msg() == "Signing in Wolf...")
    fire({"op": "play", "stage": "launched", "done": True, "ok": True,
          "email": "wolf@example.com", "message": "Wolf launched (pid 1)."})
    check("al terminar se apagan los dos a la vez", not bar() and msg() == "")
    fire({"op": "play", "stage": "settled", "email": "wolf@example.com"})
    check("y el 'settled' de después no los vuelve a encender",
          not bar() and msg() == "")

    # --- dos cuentas a la vez ----------------------------------------------
    fire({"op": "play", "stage": "launching", "email": "a@x.com",
          "message": "Launching A..."})
    fire({"op": "play", "stage": "launching", "email": "b@x.com",
          "message": "Launching B..."})
    fire({"op": "play", "stage": "launched", "done": True, "ok": True,
          "email": "a@x.com", "message": "A launched."})
    check("si queda otra cuenta en marcha, el indicador sigue y dice cuál",
          bar() and msg() == "Launching B...")
    fire({"op": "play", "stage": "launched", "done": True, "ok": True,
          "email": "b@x.com", "message": "B launched."})
    check("y se apaga con la última", not bar() and msg() == "")

    # --- un fallo de cuenta se cuenta en su tarjeta, no aquí ---------------
    fire({"op": "play", "stage": "launching", "email": "c@x.com",
          "message": "Launching C..."})
    fire({"op": "play", "stage": "error", "done": True, "ok": False,
          "email": "c@x.com", "message": "the loader exited with code 1021"})
    check("un fallo de cuenta no deja texto colgado abajo",
          not bar() and msg() == "")

    # --- una operación global sí deja su resultado a la vista --------------
    fire({"op": "update", "stage": "starting",
          "message": "Contacting the update server..."})
    check("una operación global enciende el indicador",
          bar() and msg().startswith("Contacting"))
    fire({"op": "update", "stage": "downloading", "current": 5, "total": 10})
    check("y con progreso real la barra deja de ser indeterminada",
          bar() and not page.locator("#bar-fill").evaluate(
              "e => e.classList.contains('indeterminate')"))
    fire({"op": "update", "stage": "done", "done": True, "ok": True,
          "message": "Done: STABLE-103."})
    check("al acabar, barra fuera y resultado a la vista",
          not bar() and msg() == "Done: STABLE-103." and "ok" in msg_class())

    # --- lo lento no se queda mudo -----------------------------------------
    #
    # Un lanzamiento puede tardar más de los 7 segundos que dura un mensaje de
    # resultado. El texto de algo EN MARCHA no se borra solo: se apaga cuando
    # termina, no antes.
    fire({"op": "play", "stage": "launching", "email": "slow@x.com",
          "message": "Launching Slow..."})
    page.wait_for_timeout(8000)
    check("un lanzamiento largo sigue diciendo qué hace pasados 7s",
          bar() and msg() == "Launching Slow...")

    # --- un 2FA cancelado no deja la barra girando -------------------------
    fire({"op": "play", "stage": "2fa_required", "email": "slow@x.com",
          "label": "Slow"})
    check("con el 2FA abierto se sigue viendo qué se está haciendo",
          bar() and msg() == "Launching Slow...")
    fire({"op": "play", "stage": "settled", "email": "slow@x.com"})
    check("y al cancelarlo (sólo llega 'settled') se apaga el indicador",
          not bar() and msg() == "")

    browser.close()

server.shutdown()
print("\n" + ("TODO OK" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
