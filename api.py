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
import threading
import traceback
from pathlib import Path

from core import mods as mods_mod
from core import modshub
from core import paths, prefs
from core.service import LauncherService

# What the pickers offer, and the ceiling for one file. Any image the interface
# shows has to reach it as a data URI: the page is served by a local HTTP server
# rooted inside the bundle, so it cannot open a file:// path of its own, and
# base64 makes the bytes about a third bigger on the way.
#
# 24 MB because that is where the wait starts to show. Measured end to end, from
# asking for the file to having it on screen: 4 MB takes 0.24 s, 12 MB 0.8 s,
# 24 MB 1.7 s and 40 MB 3.3 s. Animated GIFs are what pushes against this - they
# run to tens of megabytes where a photograph rarely passes three - and the
# first limit of 8 MB turned most of them away.
IMAGE_TYPES = ("Images (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)",)
IMAGE_MAX = 24 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _stored_image(name: str):
    """The image we keep under `name`, or None.

    Only ever a file directly inside our own data folder: the name comes back
    from preferences, and a name that tried to climb out of it (a slash, a
    ``..``) is not one we wrote.
    """
    if not name or name != Path(name).name:
        return None
    path = paths.app_data_dir() / name
    return path if path.is_file() else None


def _data_uri(path: Path) -> str:
    import base64
    import mimetypes

    kind = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _forget_image(stem: str) -> None:
    """Delete every copy we hold under `stem`, whatever its extension."""
    for old in paths.app_data_dir().glob(stem + ".*"):
        try:
            old.unlink()
        except OSError:
            pass


# How many of the last images picked are kept to go back to, and how much room
# they are allowed between them. Both, not either: six photographs are nothing
# and six animated GIFs are a third of a gigabyte, and neither the count nor the
# size alone says when to stop.
RECENT_KEEP = 6
RECENT_BYTES = 72 * 1024 * 1024


def _recent_dir(stem: str) -> Path:
    folder = paths.app_data_dir() / "recent" / stem
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _recent_id(data: bytes) -> str:
    """What an image is called in the store: what it IS, not where it came from.

    Content-addressed so that picking the same picture twice - which is exactly
    what happens when someone goes back to one - lands on the entry that is
    already there instead of filling the row with copies of one image.
    """
    import hashlib

    return hashlib.sha1(data).hexdigest()[:16]


def _recent_entries(stem: str) -> list[Path]:
    """The stored images, newest first."""
    folder = _recent_dir(stem)
    kept = [p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    kept.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return kept


def _recent_trim(stem: str) -> None:
    """Drop the oldest until both limits are met."""
    room = RECENT_BYTES
    for position, path in enumerate(_recent_entries(stem)):
        room -= path.stat().st_size
        if position < RECENT_KEEP and room >= 0:
            continue
        for part in (path, path.with_suffix(path.suffix + ".thumb")):
            try:
                part.unlink()
            except OSError:
                pass


def _remember_recent(stem: str, source: Path, data: bytes) -> str:
    """Keep a copy of a picked image and return the name it has in the store."""
    name = _recent_id(data)
    kept = _recent_dir(stem) / (name + source.suffix.lower())
    if kept.exists():
        kept.touch()                 # picked again: it is the newest again
    else:
        kept.write_bytes(data)
    _recent_trim(stem)
    return name


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
        # Set the first time the interface calls in. main.py waits on it to
        # tell a window that came up from one that is there but empty.
        self._alive = threading.Event()

    def _set_window(self, window) -> None:
        """Called from main.py as soon as the window exists. Underscored so that
        pywebview does not publish it as a method callable from JS."""
        self._window = window

    # --- state -------------------------------------------------------------

    @_safe
    def get_state(self):
        # The interface asks for this as soon as it loads, so it doubles as
        # proof that it did load - see the watchdog in main.py.
        self._alive.set()
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
    def cancel_launch(self, email):
        """Stop a launch or a sign-in check that is under way."""
        return self._service.cancel_account(email)

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
        # `local` is a free-form bucket for whatever a local addition needs to
        # remember. Nothing here reads it; it is stored and handed back with
        # the state. It exists because the interface has nowhere else to keep
        # anything: pywebview serves the page from a fresh profile on a new
        # port every run, so localStorage starts empty each time.
        allowed = {"remember_password", "update_first",
                   "hide_emails", "game_path", "pts_game_path", "theme",
                   "wine_binary", "wine_prefix", "local", "wallpaper", "logo",
                   "window", "mods", "sidebar"}
        clean = {k: v for k, v in (changes or {}).items() if k in allowed}
        prefs.save(**clean)
        return {"saved": clean}

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

    # --- mods ----------------------------------------------------------------

    def _mods_dir(self):
        """The mods folder of the Live installation, or None."""
        return mods_mod.mods_dir(prefs.load().get("game_path", ""))

    def _game_config(self):
        """The game's own Trove.cfg, the one it keeps its settings in.

        Under %APPDATA%/Trove, NOT the one beside the executable: that second
        one is part of the installation and holds no mod list at all.
        """
        config = mods_mod.game_config(paths.trove_appdata_dir())
        return config if config.is_file() else None

    @_safe
    def list_mods(self):
        """Every mod in the folder, without any of their preview images.

        The pictures are asked for afterwards, a handful at a time: seventy of
        them is nine megabytes of base64, and the list is useful long before
        any of that has arrived.
        """
        folder = self._mods_dir()
        if folder is None:
            return {"ok": True, "folder": "", "mods": [],
                    "detail": "Set the Live installation first: the mods folder "
                              "is the one inside it."}
        config = self._game_config()
        off, everything = mods_mod.disabled_in_config(config) if config else (set(), False)
        return {"ok": True, "folder": str(folder),
                "config": str(config) if config else "",
                "disable_all": everything,
                # The game rewrites this file when it closes, so a change made
                # while it is open would be thrown away without a word.
                "game_running": bool(self._service.running_list()),
                "mods": mods_mod.listing(folder, off)}

    @_safe
    def mod_previews(self, files):
        """The preview images of the mods named, as data URIs.

        A batch at a time, chosen by the interface: it knows which rows are on
        screen and this side does not.
        """
        folder = self._mods_dir()
        if folder is None:
            return {"ok": True, "previews": {}}
        out = {}
        for name in (files or [])[:40]:
            if name != Path(name).name:
                continue
            path = folder / name
            if path.is_file():
                out[name] = mods_mod.preview_uri(path)
        return {"ok": True, "previews": out}

    @_safe
    def set_mod_enabled(self, filename, enabled):
        """Turn one mod on or off, which is renaming it."""
        folder = self._mods_dir()
        if folder is None:
            return {"ok": False, "error": "There is no mods folder to work in."}
        mod = mods_mod.set_enabled(folder, filename, bool(enabled), self._game_config())
        return {"ok": True, "mod": mod,
                "game_running": bool(self._service.running_list())}

    @_safe
    def check_mod_updates(self):
        """Ask Mods Hub whether any installed mod has a newer version.

        The ONLY thing in this application that talks to a mod site, and only
        when this is called - which is only when the button is pressed. What
        goes out is a list of SHA-256 hashes of .tmod files; no account, no
        file name, no path.
        """
        import requests

        folder = self._mods_dir()
        if folder is None:
            return {"ok": False, "error": "There is no mods folder to check."}

        def post(url, body):
            reply = requests.post(
                url, json=body, timeout=modshub.TIMEOUT,
                headers={"User-Agent": modshub.USER_AGENT})
            reply.raise_for_status()
            return reply.json()

        paths = [p for p in folder.iterdir()
                 if p.is_file() and p.name.lower().endswith(
                     (".tmod", ".tmod" + mods_mod.DISABLED))]
        try:
            found = modshub.check(paths, post)
        except requests.RequestException as exc:
            return {"ok": False,
                    "error": f"Could not reach Mods Hub: {exc}"}

        behind = sum(1 for v in found.values() if v.get("behind"))
        known = sum(1 for v in found.values() if v.get("known"))
        return {"ok": True, "updates": found, "checked": len(paths),
                "known": known, "behind": behind}

    @_safe
    def update_mod(self, filename):
        """Replace one mod with the newest release Mods Hub has for it.

        Three things have to hold before anything on disk is touched:

          * the game is not running. Trove holds its mod files open and reads
            them as it goes, and swapping one underneath it is how you get a
            crash that looks like the mod's fault;
          * the download matches the checksum published beside it, so the bytes
            are provably the ones the author released;
          * it parses as a .tmod. A checksum matches an error page perfectly
            well if the error page is what was published.

        The file it replaces is kept, so this can be undone.
        """
        import shutil

        import requests

        from core import modshub

        folder = self._mods_dir()
        if folder is None:
            return {"ok": False, "error": "There is no mods folder to work in."}
        if filename != Path(filename).name:
            return {"ok": False, "error": "That is not a file in the mods folder."}
        if self._service.running_list():
            return {"ok": False,
                    "error": "Close Trove first. It keeps its mod files open "
                             "while it runs, and replacing one underneath it "
                             "breaks the game, not the mod."}

        path = folder / filename
        if not path.is_file():
            return {"ok": False, "error": f"{filename} is not there any more."}

        def post(url, body):
            reply = requests.post(url, json=body, timeout=modshub.TIMEOUT,
                                  headers={"User-Agent": modshub.USER_AGENT})
            reply.raise_for_status()
            return reply.json()

        try:
            news = modshub.check([path], post).get(filename) or {}
        except requests.RequestException as exc:
            return {"ok": False, "error": f"Could not reach Mods Hub: {exc}"}
        if not news.get("behind"):
            return {"ok": False, "error": "That mod is already up to date."}

        try:
            reply = requests.get(news["download_url"], timeout=120,
                                 headers={"User-Agent": modshub.USER_AGENT})
            reply.raise_for_status()
            data = reply.content
            modshub.verify(data, news.get("sha256"), news.get("size"))
        except requests.RequestException as exc:
            return {"ok": False, "error": f"The download failed: {exc}"}
        except modshub.NotWhatItSaid as exc:
            return {"ok": False,
                    "error": f"The download was discarded: {exc}. Nothing on "
                             f"disk was touched."}

        if not mods_mod.read_header(data).get("title"):
            return {"ok": False,
                    "error": "What arrived does not read as a mod file. "
                             "Nothing on disk was touched."}

        # The copy comes first: everything after this point can be undone.
        backups = paths.app_data_dir() / "mod-backups"
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, backups / filename)

        # Written beside it and moved into place, so a failure halfway cannot
        # leave half a mod where a whole one was. The NAME is kept, including a
        # .disabled on the end: replacing a mod is not turning it back on.
        temporary = path.with_suffix(path.suffix + ".tah-tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

        # The caller reads the folder again afterwards, so what goes back is
        # just what happened, not a new listing.
        return {"ok": True,
                "name": news.get("title") or filename,
                "from": news.get("installed_tag"),
                "to": news.get("latest_tag"),
                "backup": str(backups / filename)}

    @_safe
    def restore_mod(self, filename):
        """Put back the copy kept by the last update of this mod."""
        import shutil

        folder = self._mods_dir()
        if folder is None or filename != Path(filename).name:
            return {"ok": False, "error": "That is not a file in the mods folder."}
        if self._service.running_list():
            return {"ok": False, "error": "Close Trove first."}
        backup = paths.app_data_dir() / "mod-backups" / filename
        if not backup.is_file():
            return {"ok": False, "error": f"There is no kept copy of {filename}."}
        shutil.copyfile(backup, folder / filename)
        return {"ok": True}

    @_safe
    def open_page(self, url):
        """Open a mod's page in the browser.

        Only https, and only the sites the update check itself talks to. The
        address arrives from the interface, which got it from a reply off the
        network: a bridge that opened whatever it was handed would be a way to
        launch anything at all from a mod's metadata.
        """
        import webbrowser
        from urllib.parse import urlparse

        parts = urlparse(str(url or ""))
        allowed = ("trove.aallyn.net", "api.aallyn.net", "docs.aallyn.net")
        if parts.scheme != "https" or parts.hostname not in allowed:
            return {"ok": False, "error": f"Not a Mods Hub address: {url}"}
        webbrowser.open(parts.geturl())
        return {"ok": True}

    @_safe
    def open_mods_folder(self):
        folder = self._mods_dir()
        if folder is None:
            return {"ok": False, "error": "There is no mods folder to open."}
        from core.service import _open_in_file_manager

        _open_in_file_manager(folder)
        return {"ok": True}

    # --- images the user brings: background and logo -------------------------
    #
    # Both work the same way, and the copying is the point of it: pointing at
    # the original would leave the picture hanging on a file the user is free to
    # move, rename or delete without ever connecting it to this.

    def _pick_image(self, stem: str):
        """The picker, the checks and the copy. Returns the stored file, or
        None when the dialog was cancelled. Underscored so pywebview does not
        publish it: it is not something the interface calls on its own."""
        import shutil

        import webview

        if self._window is None:
            raise RuntimeError("The window is not ready yet.")
        selection = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=IMAGE_TYPES)
        if not selection:
            return None

        source = Path(selection[0] if isinstance(selection, (list, tuple)) else selection)
        suffix = source.suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            raise ValueError(f"{suffix or 'That file'} is not an image this can use. "
                             f"PNG, JPG, WEBP, GIF or BMP.")
        size = source.stat().st_size
        if size > IMAGE_MAX:
            raise ValueError(
                f"That image is {size / 1048576:.1f} MB and the limit is "
                f"{IMAGE_MAX // 1048576} MB. It has to be held in memory as text to be "
                f"drawn, and past that the window takes visibly longer to open every "
                f"time.")

        destination = paths.app_data_dir() / (stem + suffix)
        # Copy first, clean up after: a failed copy must not leave the previous
        # picture deleted and nothing in its place.
        shutil.copyfile(source, destination)
        for old in paths.app_data_dir().glob(stem + ".*"):
            if old != destination:
                try:
                    old.unlink()
                except OSError:
                    pass
        try:
            _remember_recent(stem, source, destination.read_bytes())
        except OSError:
            pass                     # the picture is in place; the row is extra
        return destination

    @_safe
    def recent_images(self, kind):
        """The last images picked for `kind`, newest first, as thumbnails.

        Thumbnails and not the pictures themselves: six of these travel to the
        interface every time the panel is drawn, and six full-size animated
        GIFs would be forty megabytes of text to look at a row of squares.
        """
        stem = "logo" if kind == "logo" else "wallpaper"
        out = []
        for path in _recent_entries(stem)[:RECENT_KEEP]:
            thumb = path.with_suffix(path.suffix + ".thumb")
            out.append({
                "id": path.stem,
                "ext": path.suffix.lstrip("."),
                "thumb": thumb.read_text(encoding="ascii") if thumb.is_file() else "",
                "bytes": path.stat().st_size,
            })
        return {"ok": True, "items": out}

    @_safe
    def save_recent_thumb(self, kind, name, thumb):
        """Keep the small version the interface drew for one stored image.

        Made there rather than here on purpose: shrinking a picture needs an
        image library, the build deliberately carries none, and the window
        already has the whole picture in hand and a canvas to draw it on.
        """
        stem = "logo" if kind == "logo" else "wallpaper"
        if not str(thumb).startswith("data:image/") or len(thumb) > 400_000:
            return {"ok": False, "error": "That is not a thumbnail."}
        for path in _recent_entries(stem):
            if path.stem == name:
                path.with_suffix(path.suffix + ".thumb").write_text(
                    thumb, encoding="ascii")
                return {"ok": True}
        return {"ok": False, "error": "That image is not kept any more."}

    @_safe
    def use_recent_image(self, kind, name):
        """Put a stored image back in use, without opening the file dialog."""
        import shutil

        stem = "logo" if kind == "logo" else "wallpaper"
        for path in _recent_entries(stem):
            if path.stem != name:
                continue
            destination = paths.app_data_dir() / (stem + path.suffix.lower())
            shutil.copyfile(path, destination)
            for old in paths.app_data_dir().glob(stem + ".*"):
                if old != destination:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            path.touch()             # used again: newest again
            settings = {**prefs.DEFAULTS[stem],
                        **(prefs.load().get(stem) or {}),
                        "file": destination.name, "recent": name}
            prefs.save(**{stem: settings})
            return {"ok": True, stem: settings, "image": _data_uri(destination)}
        return {"ok": False, "error": "That image is not kept any more."}

    @_safe
    def forget_recent_image(self, kind, name):
        """Take one image out of the row. What is in use is left alone."""
        stem = "logo" if kind == "logo" else "wallpaper"
        for path in _recent_entries(stem):
            if path.stem == name:
                for part in (path, path.with_suffix(path.suffix + ".thumb")):
                    try:
                        part.unlink()
                    except OSError:
                        pass
                return {"ok": True}
        return {"ok": True}

    @_safe
    def get_wallpaper(self):
        """The stored background as a data URI, or "" when there is none.

        Asked for once, when the interface starts. It is not part of the state:
        that travels on every refresh and this is megabytes.
        """
        settings = prefs.load().get("wallpaper") or {}
        path = _stored_image(settings.get("file", ""))
        return {"ok": True, "image": _data_uri(path) if path else ""}

    @_safe
    def browse_for_wallpaper(self):
        stored = self._pick_image("wallpaper")
        if stored is None:
            return {"ok": True, "cancelled": True}
        # The name it has in the store travels with the setting, so the row
        # underneath can mark which of its squares is the one on screen. The
        # file itself is always called wallpaper.<ext>, which says nothing about
        # WHICH picture it is.
        name = _recent_id(stored.read_bytes())
        settings = {**prefs.DEFAULTS["wallpaper"],
                    **(prefs.load().get("wallpaper") or {}),
                    "file": stored.name, "recent": name}
        prefs.save(wallpaper=settings)
        return {"ok": True, "wallpaper": settings, "image": _data_uri(stored),
                "recent": name}

    @_safe
    def clear_wallpaper(self):
        _forget_image("wallpaper")
        settings = {**prefs.DEFAULTS["wallpaper"],
                    **(prefs.load().get("wallpaper") or {}),
                    "file": "", "recent": ""}
        prefs.save(wallpaper=settings)
        return {"ok": True, "wallpaper": settings}

    @_safe
    def get_logo(self):
        """The mark in the top bar, when the user brought their own."""
        settings = prefs.load().get("logo") or {}
        path = _stored_image(settings.get("file", ""))
        return {"ok": True, "image": _data_uri(path) if path else ""}

    @_safe
    def browse_for_logo(self):
        stored = self._pick_image("logo")
        if stored is None:
            return {"ok": True, "cancelled": True}
        # The name it has in the store travels with the setting, so the row
        # underneath can mark which of its squares is the one on screen. The
        # file itself is always called logo.<ext>, which says nothing about
        # WHICH picture it is.
        name = _recent_id(stored.read_bytes())
        settings = {**prefs.DEFAULTS["logo"],
                    **(prefs.load().get("logo") or {}),
                    "file": stored.name, "recent": name}
        prefs.save(logo=settings)
        return {"ok": True, "logo": settings, "image": _data_uri(stored),
                "recent": name}

    @_safe
    def clear_logo(self):
        _forget_image("logo")
        settings = {**prefs.DEFAULTS["logo"],
                    **(prefs.load().get("logo") or {}),
                    "file": "", "recent": ""}
        prefs.save(logo=settings)
        return {"ok": True, "logo": settings}
