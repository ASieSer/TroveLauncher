#!/bin/sh
# Installs Trove Accounts Hub on Linux: environment, launcher and menu entry.
#
#   tools/install_linux.sh
#
# Why this and not a single binary like the Windows .exe:
#
# On Linux pywebview draws with WebKitGTK through PyGObject, and PyGObject is a
# SYSTEM package (python3-gi) with its typelibs and its GTK stack behind it, not
# a pip wheel. PyInstaller bundles pip packages well and system ones badly, so a
# frozen one-file build either fails to start or silently picks up the host's
# libraries anyway. Rather than ship something that breaks on the next distro,
# this sets up a proper environment once and leaves a normal desktop entry.
#
# If you would still rather have one file: `pyinstaller TroveAccountsHub.spec`
# works on Linux too, but install `qtpy PySide6-Essentials PySide6-Addons` first
# so pywebview uses Qt WebEngine, which is pip-installable and self-contained.
# The result is a ~400 MB binary. That is the honest trade.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV="$ROOT/.venv"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/512x512/apps"
DESKTOP="$APPS/trove-accounts-hub.desktop"

say() { printf '%s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

# --- what has to be there already ------------------------------------------

command -v python3 >/dev/null 2>&1 || die "python3 is not installed."

python3 - <<'PY' || die "Python 3.10 or newer is required."
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

# The window engine. It is checked against the SYSTEM python, because that is
# where the system packages live and what the venv will be allowed to see.
if ! python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null &&
   ! python3 -c 'import gi; gi.require_version("WebKit2", "4.0")' 2>/dev/null; then
    say "WebKitGTK is not visible from python3. Install it first:"
    say ""
    say "    Debian/Ubuntu : sudo apt install python3-gi gir1.2-webkit2-4.1"
    say "    Fedora        : sudo dnf install python3-gobject webkit2gtk4.1"
    say "    Arch          : sudo pacman -S python-gobject webkit2gtk-4.1"
    say ""
    die "Install the packages above and run this again."
fi

# --- the environment --------------------------------------------------------
#
# --system-site-packages is not optional: `gi` is a system package and a plain
# venv cannot see it, which is the single most common reason the app starts and
# then cannot open a window.
say "Setting up $VENV ..."
if [ ! -d "$VENV" ]; then
    python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt"

# --- the launcher -----------------------------------------------------------

cat > "$ROOT/trove-accounts-hub" <<EOF
#!/bin/sh
exec "$VENV/bin/python" "$ROOT/main.py" "\$@"
EOF
chmod +x "$ROOT/trove-accounts-hub"

# --- the menu entry ---------------------------------------------------------

mkdir -p "$APPS" "$ICONS"
cp "$ROOT/web/img/app.png" "$ICONS/trove-accounts-hub.png"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Trove Accounts Hub
Comment=Launch and manage several Trove accounts
Exec=$ROOT/trove-accounts-hub
Icon=trove-accounts-hub
Terminal=false
Categories=Game;
StartupWMClass=trove-accounts-hub
EOF
chmod +x "$DESKTOP"

command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

say ""
say "Done. Trove Accounts Hub is in your applications menu."
say "From a terminal:  $ROOT/trove-accounts-hub"
say ""
say "If it does not start, run this and paste what it says:"
say "    $VENV/bin/python $ROOT/tools/check_linux.py"
