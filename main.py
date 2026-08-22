"""Entry point: opens the WebView window and wires the UI to the service.

The interface is HTML/CSS/JS served from ``web/`` and rendered by pywebview:
WebView2 (Edge Chromium) on Windows, WebKitGTK on Linux. Calls go from JS to
Python through ``window.pywebview.api.*``; events go from Python to JS by
injecting a call to ``window.__launcherEvent`` with ``evaluate_js``.
"""

from __future__ import annotations

import json
import sys

import webview

from api import Api
from core.paths import base_dir
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

    _claim_linux_app_id()            # before creating any window, too
    scale = _enable_dpi_awareness()  # before creating any window
    width, height = _window_size(scale)

    window_ref: dict = {}
    service = LauncherService(emit=_make_emitter(window_ref),
                              log=_make_logger(window_ref))
    api = Api(service)

    window = webview.create_window(
        APP_TITLE,
        str(index),
        js_api=api,
        width=width,
        height=height,
        min_size=MIN_WINDOW_SIZE,
        background_color="#12141c",
        text_select=False,
    )
    window_ref["window"] = window
    api._set_window(window)

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
            return _fatal(
                f"Could not open the window:\n{exc}\n\n"
                f"Trove Accounts Hub draws its interface with the Microsoft "
                f"Edge WebView2 runtime. Windows 11 includes it and Windows 10 "
                f"normally gets it with Edge; if this machine does not have it, "
                f"install the Evergreen Runtime from:\n\n"
                f"    https://developer.microsoft.com/microsoft-edge/webview2/")
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
