"""Punto de entrada: abre la ventana WebView y conecta la interfaz con el servicio.

La interfaz es HTML/CSS/JS servido desde ``web/`` y renderizado por pywebview:
WebView2 (Edge Chromium) en Windows y WebKitGTK en Linux. Las llamadas van de JS
a Python por ``window.pywebview.api.*``; los eventos van de Python a JS
inyectando una llamada a ``window.__launcherEvent`` con ``evaluate_js``.
"""

from __future__ import annotations

import json
import sys

import webview

from api import Api
from core.paths import base_dir
from core.service import LauncherService

APP_TITLE = "Trove Accounts Hub"
# Tamaño deseado en píxeles CSS: lo que la hoja de estilo da por supuesto. En
# pantalla se multiplica por la escala del monitor (ver _enable_dpi_awareness).
WINDOW_SIZE = (1060, 760)
MIN_WINDOW_SIZE = (820, 620)


def _enable_dpi_awareness() -> float:
    """Declara el proceso consciente del DPI y devuelve la escala del monitor.

    Sin esto, Windows nos miente diciendo que trabajamos a 96 DPI mientras
    WebView2 sí dibuja a la escala real del escritorio: el contenido sale
    ampliado y recortado por la derecha. Al declararnos conscientes, el ancho
    en píxeles CSS pasa a ser (píxeles de ventana / escala), que es justo lo que
    la hoja de estilo espera.
    """
    if sys.platform != "win32":
        return 1.0

    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # Windows anteriores a 8.1
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
    """Tamaño de ventana en píxeles lógicos, recortado al escritorio disponible.

    pywebview pide el tamaño en unidades lógicas y ya lo multiplica por la escala
    del monitor, así que aquí NO volvemos a escalar: hacerlo daba una ventana de
    1656x1187 en un escritorio de 1152 de alto. Lo que sí hay que convertir es el
    límite de pantalla, que llega en píxeles físicos.
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
    """Empuja un evento del servicio hacia la interfaz.

    ``json.dumps`` con ``ensure_ascii`` escapa todo lo que no sea ASCII, así que
    lo que inyectamos es siempre un literal JS seguro. Si la ventana ya se cerró
    (o aún no existe) el evento se descarta sin ruido: es progreso, no estado.
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


def main() -> int:
    index = base_dir() / "web" / "index.html"
    if not index.is_file():
        print(f"UI not found at {index}", file=sys.stderr)
        return 1

    scale = _enable_dpi_awareness()  # antes de crear ninguna ventana
    width, height = _window_size(scale)

    window_ref: dict = {}
    service = LauncherService(emit=_make_emitter(window_ref))
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

    # debug=True abre las DevTools de WebView2 con F12: útil mientras montamos
    # la interfaz. Se activa pasando --debug al arrancar.
    try:
        webview.start(debug="--debug" in sys.argv)
    except Exception as exc:
        # El fallo típico fuera de Windows es no tener ningún motor detrás de
        # pywebview. Decirlo con su nombre ahorra media hora de rastreo.
        if sys.platform != "win32":
            print(f"No se pudo abrir la ventana: {exc}\n\n"
                  f"En Linux pywebview necesita WebKitGTK. En Debian/Ubuntu:\n"
                  f"  sudo apt install python3-gi gir1.2-webkit2-4.1 "
                  f"libcairo2-dev\n"
                  f"  pip install pywebview[gtk]", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
