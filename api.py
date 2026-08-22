"""Bridge between the web interface and ``core.service``.

pywebview exposes every public method of this class at
``window.pywebview.api.<name>`` and returns a promise with whatever it returns.

Two rules for everything in here:

  * Nothing raises towards JS. A failure comes back as
    ``{"ok": false, "error": "..."}`` so the interface can show it as-is instead
    of breaking on a rejected promise.
  * Everything returned must be JSON-serialisable (no Path objects, no
    arbitrary instances).

The instance attributes carry a leading underscore on purpose: pywebview walks
the public attributes of the ``js_api`` object to expose them to the JS side,
and on reaching the window's native object it falls into infinite recursion
(``window.native.AccessibilityObject.Bounds.Empty.Empty...``). With the
underscore it skips them and publishes only the methods.
"""

from __future__ import annotations

import functools
import traceback
from pathlib import Path

from core import prefs
from core.service import LauncherService


def _safe(method):
    """Turns any exception from the service into an error reply."""
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
        self._window = None  # filled in by main.py once the window exists

    def _set_window(self, window) -> None:
        """Called from main.py as soon as the window exists. Underscored so that
        pywebview does not publish it as a method callable from JS."""
        self._window = window

    # --- state -------------------------------------------------------------

    @_safe
    def get_state(self):
        return {"state": self._service.state()}

    # --- play --------------------------------------------------------------

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

    # --- accounts ------------------------------------------------------------

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

    # --- groups -------------------------------------------------------------

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

    # --- maintenance ------------------------------------------------------

    @_safe
    def check(self, target):
        return self._service.check(target)

    @_safe
    def update(self, target):
        return self._service.update(target)

    @_safe
    def repair(self, target):
        return self._service.repair(target)

    # --- installations and folders -------------------------------------------

    @_safe
    def set_install(self, path, kind):
        return self._service.set_install(path, kind)

    @_safe
    def browse_for_install(self, kind):
        """Opens the native folder picker, validates the choice and sets it as
        the Live or the PTS installation."""
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

    # --- preferences -------------------------------------------------------

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

    # --- appearance ---------------------------------------------------------

    @_safe
    def set_window_icon(self, frames):
        """Repaint the title-bar and taskbar icon to match the theme.

        `frames` is what the interface drew: {"16": "<base64 RGBA>", ...}. The
        pixels come from the front end because it is the side that knows the
        accent and can draw; all that is left here is the Win32 part.
        """
        import base64

        from core import winicon

        if self._window is None:
            return {"ok": False, "error": "The window is not ready yet."}
        try:
            hwnd = int(self._window.native.Handle.ToInt64())
        except Exception:
            return {"ok": True, "applied": False}   # not a Win32 window

        decoded = {}
        for size, data in (frames or {}).items():
            try:
                decoded[int(size)] = base64.b64decode(data)
            except Exception:
                continue
        return {"ok": True, "applied": winicon.apply(hwnd, decoded)}
