"""The window's icon, replaced while the application runs.

The icon in the title bar and the one on the taskbar button are a Win32 property
of the window (``WM_SETICON``), not something baked into the executable, so they
can follow the theme the same way every other surface does.

The pixels are not drawn here. The interface already knows the accent and
already draws, so it renders the cube on a canvas and hands over the raw RGBA
(see ``paintWindowIcon`` in web/js/theme.js). This module only turns those bytes
into an ``HICON`` and hangs it on the window, which is the part JavaScript
cannot do. That also keeps Pillow out of the packaged application: it is needed
to build the .ico that goes *inside* the .exe, and nowhere at run time.

Windows-only, and deliberately so. On X11 the equivalent exists
(``Gtk.Window.set_icon`` writes _NET_WM_ICON), but on Wayland there is no
protocol for a window to hand the compositor its own icon at all: the icon comes
from the .desktop entry the window is matched to by its app_id. Since the
desktops people actually run are split between the two, Linux keeps the static
cube from the desktop entry rather than a feature that works on half of them.
See ``_claim_linux_app_id`` in main.py for the matching that makes that work.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1

# Which size feeds which slot. Small is the title bar, big is the taskbar button
# and alt-tab; Windows scales from these for whatever the display asks for.
SLOTS = ((ICON_SMALL, 16), (ICON_BIG, 32))

# The icons currently hung on each window. They have to outlive the call: the
# window keeps using the handle, and freeing it early leaves a blank square.
# The previous pair is destroyed only once the new one is in place.
_LIVE: dict[int, list[int]] = {}


class _IconInfo(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _win32():
    """user32 and gdi32, with the signatures that matter declared.

    Declaring them is not tidiness: without argtypes ctypes guesses `int` for
    handles, and a 64-bit handle then raises "int too long to convert" the
    moment one lands above 2^31.
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    return user32, gdi32


def _hicon(rgba: bytes, size: int) -> int:
    """An HICON from loose RGBA pixels, without going through a file."""
    user32, gdi32 = _win32()

    header = _BitmapInfoHeader()
    header.biSize = ctypes.sizeof(_BitmapInfoHeader)
    header.biWidth = size
    header.biHeight = -size          # negative: top-down, the way canvas hands it over
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0         # BI_RGB

    bits = ctypes.c_void_p()
    screen = user32.GetDC(None)
    colour = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0,
                                    ctypes.byref(bits), None, 0)
    user32.ReleaseDC(None, screen)
    if not colour:
        raise OSError("CreateDIBSection failed")

    # Windows wants BGRA with the colour premultiplied by the alpha. Handing it
    # straight RGBA leaves a dark halo around every antialiased edge.
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        red, green, blue, alpha = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        out[i] = blue * alpha // 255
        out[i + 1] = green * alpha // 255
        out[i + 2] = red * alpha // 255
        out[i + 3] = alpha
    ctypes.memmove(bits, bytes(out), len(out))

    # The mask is required but unused: the alpha channel above does the shaping.
    mask = gdi32.CreateBitmap(size, size, 1, 1, None)
    info = _IconInfo(True, 0, 0, mask, colour)
    icon = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(colour)
    gdi32.DeleteObject(mask)
    if not icon:
        raise OSError("CreateIconIndirect failed")
    return icon


def apply(hwnd: int, frames: dict[int, bytes]) -> bool:
    """Hang `frames` on the window. `frames` maps a pixel size to its RGBA.

    Returns False when there is nothing to do rather than raising: an icon that
    will not change is not a reason to stop the application, and the caller has
    no better answer than to carry on either.
    """
    if sys.platform != "win32" or not hwnd:
        return False

    user32, _gdi32 = _win32()
    fresh = []
    try:
        for slot, size in SLOTS:
            rgba = frames.get(size)
            if not rgba or len(rgba) != size * size * 4:
                continue
            icon = _hicon(rgba, size)
            fresh.append(icon)
            user32.SendMessageW(hwnd, WM_SETICON, slot, icon)
    except OSError:
        for icon in fresh:
            user32.DestroyIcon(icon)
        return False

    if not fresh:
        return False

    # The old pair is only released now that the window is no longer using it.
    for icon in _LIVE.get(hwnd, []):
        user32.DestroyIcon(icon)
    _LIVE[hwnd] = fresh
    return True
