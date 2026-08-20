"""Puente entre la interfaz web y ``core.service``.

pywebview expone cada método público de esta clase en
``window.pywebview.api.<nombre>`` y devuelve una promesa con lo que retorne.

Dos reglas para todo lo que hay aquí:

  * Nada lanza excepciones hacia JS. Un fallo vuelve como
    ``{"ok": false, "error": "..."}`` para que la interfaz pueda enseñarlo tal
    cual en lugar de romperse con una promesa rechazada.
  * Todo lo devuelto debe ser serializable a JSON (nada de Path ni objetos).

Los atributos de instancia van con guion bajo a propósito: pywebview recorre los
atributos públicos del objeto ``js_api`` para exponerlos al lado JS, y al llegar
al objeto nativo de la ventana entra en una recursión infinita
(``window.native.AccessibilityObject.Bounds.Empty.Empty...``). Con el guion bajo
los ignora y sólo publica los métodos.
"""

from __future__ import annotations

import functools
import traceback
from pathlib import Path

from core import prefs
from core.service import LauncherService


def _safe(method):
    """Convierte cualquier excepción del servicio en una respuesta de error."""
    @functools.wraps(method)
    def _wrapper(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc) or exc.__class__.__name__}
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": True, "data": result}
    return _wrapper


class Api:
    def __init__(self, service: LauncherService):
        self._service = service
        self._window = None  # lo rellena main.py una vez creada la ventana

    def _set_window(self, window) -> None:
        """Llamado desde main.py en cuanto existe la ventana. Con guion bajo para
        que pywebview no lo publique como método invocable desde JS."""
        self._window = window

    # --- estado -------------------------------------------------------------

    @_safe
    def get_state(self):
        return {"state": self._service.state()}

    # --- jugar --------------------------------------------------------------

    @_safe
    def play(self, options):
        options = options or {}
        return self._service.play(
            email=options.get("email", ""),
            password=options.get("password", ""),
            update_first=options.get("update_first"),
            remember_password=options.get("remember_password"),
        )

    @_safe
    def test_login(self, options):
        options = options or {}
        return self._service.test_login(
            email=options.get("email", ""),
            password=options.get("password", ""),
            remember_password=options.get("remember_password"),
        )

    @_safe
    def stop(self, pid):
        return self._service.stop(pid)

    @_safe
    def submit_2fa(self, email, code):
        return self._service.submit_2fa(email, code)

    @_safe
    def cancel_2fa(self, email):
        return self._service.cancel_2fa(email)

    # --- cuentas ------------------------------------------------------------

    @_safe
    def add_account(self, options):
        options = options or {}
        return self._service.add_account(
            email=options.get("email", ""),
            password=options.get("password", ""),
            name=options.get("name", ""),
            region=options.get("region", ""),
            group=options.get("group", ""),
            remember_password=options.get("remember_password", True),
        )

    @_safe
    def update_account(self, email, fields):
        return self._service.update_account(email, **(fields or {}))

    @_safe
    def remove_account(self, email):
        return self._service.remove_account(email)

    @_safe
    def logout(self, email):
        return self._service.logout(email)

    @_safe
    def set_password(self, email, password):
        return self._service.set_password(email, password)

    # --- grupos -------------------------------------------------------------

    @_safe
    def create_group(self, name):
        return self._service.create_group(name)

    @_safe
    def update_group(self, group_id, fields):
        return self._service.update_group(group_id, **(fields or {}))

    @_safe
    def delete_group(self, group_id):
        return self._service.delete_group(group_id)

    @_safe
    def reorder(self, payload):
        payload = payload or {}
        return self._service.reorder(groups=payload.get("groups"),
                                     accounts=payload.get("accounts"))

    # --- mantenimiento ------------------------------------------------------

    @_safe
    def check(self, target):
        return self._service.check(target)

    @_safe
    def update(self, target):
        return self._service.update(target)

    @_safe
    def repair(self, target):
        return self._service.repair(target)

    # --- instalaciones y carpetas -------------------------------------------

    @_safe
    def set_install(self, path, kind):
        return self._service.set_install(path, kind)

    @_safe
    def browse_for_install(self, kind):
        """Abre el selector de carpetas nativo, valida lo elegido y lo fija como
        instalación de Live o de PTS."""
        import webview

        if self._window is None:
            return {"ok": False, "error": "The window is not ready yet."}
        selection = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not selection:
            return {"ok": True, "cancelled": True}
        folder = selection[0] if isinstance(selection, (list, tuple)) else selection

        added = self._service.add_custom_dir(str(folder), Path(folder).name)
        if not added.get("ok", True):
            return added
        self._service.set_install(str(folder), kind or "live")
        return {"ok": True, "path": str(folder), "installs": added.get("installs", [])}

    @_safe
    def remove_custom_dir(self, path):
        return self._service.remove_custom_dir(path)

    @_safe
    def rescan_installs(self):
        return self._service.rescan_installs()

    @_safe
    def open_folder(self, kind):
        return self._service.open_folder(kind)

    # --- preferencias -------------------------------------------------------

    @_safe
    def save_prefs(self, changes):
        allowed = {"remember_password", "update_first", "reparent_glyph",
                   "hide_emails", "game_path", "pts_game_path", "theme",
                   "wine_binary", "wine_prefix"}
        clean = {k: v for k, v in (changes or {}).items() if k in allowed}
        prefs.save(**clean)
        return {"saved": clean}

    @_safe
    def set_auto_relog(self, email, enabled):
        return self._service.set_auto_relog(email, enabled)
