"""Entry point: opens the WebView window and wires the UI to the service.

The interface is HTML/CSS/JS served from ``web/`` and rendered by pywebview:
WebView2 (Edge Chromium) on Windows, WebKitGTK on Linux. Calls go from JS to
Python through ``window.pywebview.api.*``; events go from Python to JS by
injecting a call to ``window.__launcherEvent`` with ``evaluate_js``.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import webview

from api import Api
from core import prefs
from core.paths import app_data_dir, base_dir
from core.service import LauncherService

APP_TITLE = "Trove Accounts Hub"
# Wanted size in CSS pixels: what the stylesheet assumes. On screen it gets
# multiplied by the monitor's scale (see _enable_dpi_awareness).
WINDOW_SIZE = (1060, 760)
MIN_WINDOW_SIZE = (820, 620)


def _enable_dpi_awareness() -> float:
    """Declare the process DPI-aware and return the monitor's scale.

    Without this, Windows lies to us and reports 96 DPI while WebView2 does draw
    at the desktop's real scale: the content comes out enlarged and clipped on
    the right. Once we declare ourselves aware, the width in CSS pixels becomes
    (window pixels / scale), which is exactly what the stylesheet expects.
    """
    if sys.platform != "win32":
        return 1.0

    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # Windows older than 8.1
        except Exception:
            return 1.0

    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return (dpi or 96) / 96.0
    except Exception:
        return 1.0


def _window_size(scale: float) -> tuple[int, int]:
    """Window size in logical pixels, clamped to the available desktop.

    pywebview takes the size in logical units and already multiplies it by the
    monitor's scale, so we do NOT scale again here: doing that gave a 1656x1187
    window on a desktop 1152 pixels tall. What does need converting is the
    screen limit, which arrives in physical pixels.
    """
    width, height = WINDOW_SIZE
    if sys.platform != "win32":
        return width, height

    import ctypes

    try:
        screen_w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        if screen_w and screen_h and scale > 0:
            width = min(width, int(screen_w / scale * 0.92))
            height = min(height, int(screen_h / scale * 0.88))
    except Exception:
        pass
    return width, height


def _make_emitter(window_ref: dict):
    """Push a service event towards the interface.

    ``json.dumps`` with ``ensure_ascii`` escapes everything non-ASCII, so what we
    inject is always a safe JS literal. If the window has already closed (or does
    not exist yet) the event is dropped quietly: it is progress, not state.
    """
    def _emit(payload: dict) -> None:
        window = window_ref.get("window")
        if window is None:
            return
        try:
            window.evaluate_js(f"window.__launcherEvent && window.__launcherEvent({json.dumps(payload)})")
        except Exception:
            pass
    return _emit


# What Linux desktops use to match this window to its .desktop entry, and
# therefore which icon they show for it.
LINUX_APP_ID = "trove-accounts-hub"


def _claim_linux_app_id() -> None:
    """Name the process so the desktop can find our .desktop entry.

    GTK builds both the X11 WM_CLASS and the Wayland app_id from
    ``g_get_prgname()``, which defaults to the basename of argv[0] - "python3"
    when started the usual way. A window calling itself python3 matches no
    desktop entry, so the dock shows a generic icon instead of ours.

    On Wayland this is the ONLY way the window gets an icon at all: there is no
    protocol for a window to hand the compositor its own, so the icon always
    comes from the entry this name points at. It has to match StartupWMClass in
    tools/install_linux.sh.

    Must run before the window is created. Quiet if GTK is not the backend.
    """
    if sys.platform == "win32":
        return
    try:
        import gi                                        # noqa: PLC0415

        from gi.repository import GLib                   # noqa: PLC0415

        GLib.set_prgname(LINUX_APP_ID)
        GLib.set_application_name(APP_TITLE)
    except Exception:
        pass


def _claim_windows_app_id() -> None:
    """Give the process its own taskbar identity.

    Without this, Windows ties the taskbar button to the executable's path and
    paints it with the icon compiled into the .exe - so the live icon set with
    WM_SETICON reaches the title bar and alt-tab but never the taskbar, and the
    cube there stays the default green whatever accent is on.

    Declaring an explicit AppUserModelID detaches the button from the file, and
    the window's own ICON_BIG becomes what the taskbar shows. See
    core/winicon.py for the other half.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ASieSer.TroveAccountsHub")
    except Exception:
        pass


def _make_logger(window_ref: dict):
    """The service's log line: to the console if there is one, and to the panel.

    Packaged with --windowed there is no console at all, so printing on its own
    would throw away everything the launcher has to say about a launch. The
    interface already has somewhere to put it - the log box in Settings - and
    this is what feeds it.
    """
    emit = _make_emitter(window_ref)

    def _log(message) -> None:
        text = str(message)
        if sys.stdout is not None:
            print(text)
        emit({"op": "app", "stage": "log", "message": text})
    return _log


# The four EdgeUpdate client ids the WebView2 runtime registers itself under:
# the stable one plus beta, dev and canary, any of which is enough.
_WEBVIEW2_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",   # Evergreen runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",   # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",   # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",   # Canary
)

_WEBVIEW2_DOWNLOAD = "    https://developer.microsoft.com/microsoft-edge/webview2/"

WEBVIEW2_MISSING = (
    "Trove Accounts Hub draws its interface with the Microsoft Edge WebView2 "
    "runtime, and this machine does not have it.\n\n"
    "Install the Evergreen Runtime (free, from Microsoft) and start the "
    "application again:\n\n" + _WEBVIEW2_DOWNLOAD + "\n\n"
    "Windows 11 includes it and Windows 10 normally gets it with Edge, so a "
    "machine without it is usually one where Edge has never been updated."
)

WEBVIEW2_BROKEN = (
    "The Microsoft Edge WebView2 runtime is registered on this machine, but "
    "its files are not where the registry says they are:\n\n"
    "{where}\n\n"
    "That is what an interrupted update leaves behind, and it is why the "
    "window would open empty. Reinstalling the Evergreen Runtime puts it back "
    "- the installer repairs an existing installation, it does not ask "
    "anything:\n\n" + _WEBVIEW2_DOWNLOAD
)

# Kept for anyone importing it by the old name.
WEBVIEW2_HELP = WEBVIEW2_MISSING


def _webview2_trouble():
    """What is wrong with the WebView2 runtime, if anything, BEFORE we start.

    Returns None when there is nothing to say, or the message to show.

    Worth asking rather than leaving to fail, because neither failure is loud.
    With no runtime at all pywebview quietly falls back to MSHTML, the old
    Internet Explorer engine, which cannot run a line of this interface. With a
    runtime whose files have gone - an update that stopped halfway - WebView2
    itself fails with 0x80070002, which pywebview logs and swallows. Both leave
    the same thing on screen: a window painted in ``background_color`` and
    nothing in it.

    The version comes from the same registry keys pywebview reads, so the
    answer matches the decision it is about to make. The folder is then checked
    too, since a version there only says something was installed once. Being
    wrong in the strict direction would block a machine that works, so a
    missing folder is only reported when the registry says where it should be
    and it is definitely not there.
    """
    if sys.platform != "win32":
        return None

    import winreg

    seen = []          # (version, expected executable) for every client found
    prefixes = (r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients")
    for client in _WEBVIEW2_CLIENTS:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for prefix in prefixes:
                try:
                    with winreg.OpenKey(root, prefix + "\\" + client) as key:
                        version = str(winreg.QueryValueEx(key, "pv")[0])
                        try:
                            where = str(winreg.QueryValueEx(key, "location")[0])
                        except OSError:
                            where = ""      # per-user installs may not say
                except OSError:
                    continue
                if not version or version == "0":
                    continue
                if not where:
                    return None             # installed, and unverifiable: fine
                exe = Path(where) / version / "msedgewebview2.exe"
                if exe.is_file():
                    return None             # installed and really there
                seen.append(str(exe))

    if seen:
        return WEBVIEW2_BROKEN.format(where="    " + seen[0])
    return WEBVIEW2_MISSING


# How long the interface is given to call in before we accept it is not coming.
# Generous on purpose: a cold WebView2 on a slow disk can take a few seconds,
# and a false alarm here would be worse than the silence it replaces.
UI_TIMEOUT = 25.0


def _start_log():
    """Send pywebview's own log to a file, and return where it went.

    Every way this window can come up empty is silent. Without the WebView2
    runtime pywebview falls back to MSHTML with nothing but a warning; with it,
    a WebView2 environment that fails to start is one ``logger.error`` and a
    return. Both are printed to a console the packaged application does not
    have, so what the user sees is a dark rectangle and no reason for it. In a
    file they become the one thing worth asking for.
    """
    path = app_data_dir() / "startup.log"
    try:
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError:
        return path                      # a log we cannot write is not fatal
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)       # everything else: only what went wrong
    engine = logging.getLogger("pywebview")
    engine.setLevel(logging.DEBUG)       # this one in full: it picks the engine
    return path


def _tail(path, lines: int = 14) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:]).strip()
    except OSError:
        return ""


def _watch_for_an_empty_window(api, log_path) -> None:
    """Say something when the window is up but the interface never arrives.

    The interface asks for the state the moment it loads, so that call is the
    signal. Nothing arriving means the page did not run: the wrong engine, a
    WebView2 environment that could not start (no room on the disk, no writable
    TEMP, an antivirus in the way). The reason is in the log by then, so it goes
    in the message rather than being left for the user to find.
    """
    def _wait() -> None:
        if api._alive.wait(UI_TIMEOUT):
            return
        detail = _tail(log_path)
        _fatal("The window opened but the interface never loaded."
               "\n\nUsually the Edge WebView2 engine could not start: no room "
               "left on the disk, no writable TEMP folder, or an antivirus "
               "stopping it."
               "\n\nWhat the log says:\n\n" + (detail or "(nothing)") +
               "\n\nFull log:\n\n    " + str(log_path))

    threading.Thread(target=_wait, daemon=True, name="trove-watchdog").start()


def _saved_geometry(width: int, height: int) -> dict:
    """Where to put the window, from where it was left.

    Checked against the screens that exist right now: a window remembered on a
    second monitor that is no longer plugged in would open at coordinates
    nobody can reach, and "it disappeared" is a worse bug than "it opened in
    the middle". Only the position is dropped in that case - the size is still
    good.
    """
    saved = prefs.load().get("window") or {}
    geometry = {"width": int(saved.get("width") or width),
                "height": int(saved.get("height") or height),
                "maximized": bool(saved.get("maximized"))}
    if saved.get("x") is None or saved.get("y") is None:
        return geometry

    x, y = int(saved["x"]), int(saved["y"])
    try:
        import webview

        screens = webview.screens
    except Exception:
        screens = []
    # Enough of the title bar has to land on some screen to be grabbable.
    margin = 80
    for screen in screens or []:
        if (screen.x - margin <= x <= screen.x + screen.width - margin
                and screen.y <= y <= screen.y + screen.height - margin):
            geometry["x"], geometry["y"] = x, y
            break
    return geometry


def _remember_window(window) -> None:
    """Write the window's place down when it closes.

    On closing rather than on every move: dragging a window fires a stream of
    those, and preferences are rewritten whole each time (see prefs.save).
    """
    def _save():
        try:
            state = {"x": int(window.x), "y": int(window.y),
                     "width": int(window.width), "height": int(window.height)}
        except Exception:
            return          # no window left to ask, on some platforms
        prefs.save(window=state)

    window.events.closing += _save


def _fatal(message: str) -> int:
    """Report a start-up failure and return the exit code.

    Frozen with --windowed there is no console: anything printed goes nowhere
    and the user gets an executable that appears to do nothing at all. So when
    there is no stdout to write to, the message goes into a native message box
    instead. Run from a terminal, it still just prints.
    """
    if sys.stdout is not None:
        print(message, file=sys.stderr)
    elif sys.platform == "win32":
        import ctypes

        MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, MB_ICONERROR)
    return 1


def main() -> int:
    index = base_dir() / "web" / "index.html"
    if not index.is_file():
        return _fatal(f"The interface is missing: {index}\n\n"
                      f"If this is the packaged build, the executable is "
                      f"incomplete; download it again.")

    trouble = _webview2_trouble()
    if trouble:
        return _fatal(trouble)

    log_path = _start_log()

    _claim_windows_app_id()          # before the taskbar button exists
    _claim_linux_app_id()            # before creating any window, too
    scale = _enable_dpi_awareness()  # before creating any window
    width, height = _window_size(scale)
    geometry = _saved_geometry(width, height)

    window_ref: dict = {}
    service = LauncherService(emit=_make_emitter(window_ref),
                              log=_make_logger(window_ref))
    api = Api(service)

    window = webview.create_window(
        APP_TITLE,
        str(index),
        js_api=api,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry.get("x"),
        y=geometry.get("y"),
        min_size=MIN_WINDOW_SIZE,
        background_color="#12141c",
        text_select=False,
    )
    window_ref["window"] = window
    api._set_window(window)
    _remember_window(window)
    _watch_for_an_empty_window(api, log_path)

    # debug=True opens WebView2's DevTools with F12: handy while building the
    # interface. Turn it on by passing --debug at start-up.
    try:
        webview.start(debug="--debug" in sys.argv)
    except Exception as exc:
        # Neither platform can draw a window without an engine behind pywebview,
        # and on both the error it raises says nothing useful on its own.
        if sys.platform == "win32":
            # On Windows that engine is the WebView2 runtime. It ships with
            # Windows 11 and arrives with Edge on Windows 10, so it is normally
            # there - but a machine that has never updated Edge will not have
            # it, and then this is the only thing that goes wrong.
            return _fatal(f"Could not open the window:\n{exc}\n\n"
                          + WEBVIEW2_MISSING)
        return _fatal(
            f"Could not open the window: {exc}\n\n"
            f"On Linux pywebview needs an engine behind it. Two options:\n\n"
            f"  - WebKitGTK, the system one (light):\n"
            f"      sudo apt install python3-gi gir1.2-webkit2-4.1\n"
            f"      pip install pywebview[gtk]\n\n"
            f"  - Qt WebEngine, which brings its own Chromium (nothing from "
            f"the system):\n"
            f"      pip install qtpy PySide6-Essentials PySide6-Addons\n\n"
            f"`python tools/check_linux.py` says which of the two this "
            f"machine can see.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
